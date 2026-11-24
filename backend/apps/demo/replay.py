"""Run a demo history plan through the real services.

Every ticket is created, commented, moved and triaged by the same code as in
production, with the planned instant as `now`, so deadlines, pauses, business
minutes and events are consistent by construction. Runs in the caller's
transaction (the system context), with notifications suppressed.
"""

import datetime as dt

from django.db import connection

from apps.accounts.models import User
from apps.demo.plan import COMMENT, TRANSITION, UPDATE
from apps.tickets.models import Category
from apps.tickets.services.comments import add_comment
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.sla_alerts import raise_sla_alerts
from apps.tickets.services.state_machine import transition
from apps.tickets.services.triage import update_ticket

# updated_at is auto_now: each save during the replay stamps today's date. Set
# it to the ticket's last activity instead, as if it had been saved back then.
LAST_ACTIVITY = """
UPDATE ticket
SET updated_at = latest.at
FROM (
    SELECT ticket_id, max(created_at) AS at
    FROM (
        SELECT ticket_id, created_at FROM ticket_event
        UNION ALL
        SELECT ticket_id, created_at FROM ticket_comment
    ) AS activity
    GROUP BY ticket_id
) AS latest
WHERE ticket.id = latest.ticket_id
"""


def replay(plans, catalog, *, now: dt.datetime) -> int:
    """Run the plans in order and return the number of SLA alerts raised at `now`."""
    retirees = _retirees(catalog)
    # Active during the replay: creation rejects an inactive requester or category,
    # and assignment an inactive agent. The plan only gives them tickets from
    # before they left, so deactivating them at the end is enough.
    _set_active(retirees, True)
    for plan in plans:
        _replay_ticket(plan)
    _set_active(retirees, False)

    with connection.cursor() as cursor:
        cursor.execute(LAST_ACTIVITY)
    # What the alert job would find at `now`: open tickets past their warning or
    # deadline. Earlier alerts are not recreated: past breaches are measured from
    # the ticket's columns.
    return len(raise_sla_alerts(now))


def _replay_ticket(plan) -> None:
    ticket = create_ticket(
        requester=plan.requester,
        title=plan.title,
        description=plan.description,
        category=plan.category,
        priority=plan.priority,
        now=plan.created_at,
    )
    for step in plan.steps:
        if step.kind == COMMENT:
            add_comment(ticket, step.actor, step.body, is_internal=step.internal, now=step.at)
        elif step.kind == TRANSITION:
            ticket = transition(ticket, step.status, step.actor, now=step.at)
        elif step.kind == UPDATE:
            ticket = update_ticket(ticket, step.actor, **step.changes, now=step.at)
        else:
            raise ValueError(f"Unknown step kind {step.kind!r}.")


def _retirees(catalog) -> list:
    people = [*catalog.agents, *(user for users in catalog.clients.values() for user in users)]
    return [item for item in [*people, *catalog.categories] if not item.is_active]


def _set_active(items, active: bool) -> None:
    """In the database and on the objects the plans hold (the services check them)."""
    for item in items:
        item.is_active = active
    for model in (User, Category):
        pks = [item.pk for item in items if isinstance(item, model)]
        model.objects.filter(pk__in=pks).update(is_active=active)
