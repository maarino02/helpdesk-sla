"""Reprojection of SLA deadlines after a calendar change (ADR-0002, ADR-0005).

When a calendar changes (a holiday added, working hours changed) or an
organization switches calendars, the business time left on each open ticket is
measured with the old calendar and placed again with the new one. Time already
consumed is not touched, nor is the resolution target, so consumed = target -
left still holds. Deadlines already breached never move: a calendar change cannot
erase a breach.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.sla.business_time import Schedule, reproject
from apps.tickets.models import PAUSED_STATUSES, EventType, Status, Ticket, TicketEvent
from apps.tickets.services.deadlines import refresh_warnings

# Tickets whose clock is running or paused. Closed ones are finished.
OPEN_STATUSES = [Status.NEW, Status.OPEN, Status.PENDING, Status.RESOLVED]
# The first response clock never pauses; the alerts watch it in these statuses.
FIRST_RESPONSE_STATUSES = [Status.NEW, Status.OPEN, Status.PENDING]


def tickets_using_calendar(calendar_id: int) -> QuerySet[Ticket]:
    """Tickets of every organization that uses a calendar."""
    return Ticket.objects.filter(organization__business_calendar_id=calendar_id)


def tickets_of_organization(organization_id: int) -> QuerySet[Ticket]:
    return Ticket.objects.filter(organization_id=organization_id)


def recalculate_deadlines(
    tickets: QuerySet[Ticket],
    old: Schedule,
    new: Schedule,
    *,
    now: datetime | None = None,
    context: dict | None = None,
) -> int:
    """Reproject the deadlines of the open tickets in `tickets`.

    `old` must be the schedule before the change (see lock_and_snapshot) and `new`
    the one after it. `context` is added to each event's metadata, e.g. which
    calendar changed. Returns the number of tickets whose deadlines moved.
    """
    if old == new:
        # Nothing that affects business time changed (e.g. only the name).
        return 0

    with transaction.atomic():
        # One query, ordered by id: two recalculations lock their rows in the same
        # order, so they cannot deadlock each other; transitions, triage and
        # comments lock a single ticket, so they cannot close a cycle either.
        # of=("self",): the filter may join organization, whose rows must not be
        # locked too (ticket creation locks them to reserve numbers).
        locked = list(
            tickets.filter(status__in=OPEN_STATUSES).select_for_update(of=("self",)).order_by("id")
        )
        now = now or timezone.now()
        return sum(_reproject_ticket(ticket, old, new, now, context or {}) for ticket in locked)


def _reproject_ticket(ticket: Ticket, old: Schedule, new: Schedule, now, context) -> bool:
    before = {
        "resolution_due_at": ticket.resolution_due_at,
        "first_response_due_at": ticket.first_response_due_at,
    }

    # While paused, the clock stopped at sla_paused_at: what was left is measured
    # from there. Resuming will add the pause, measured with the new calendar, so
    # projecting from now would count the stretch since the pause twice.
    anchor = ticket.sla_paused_at if ticket.status in PAUSED_STATUSES else now
    if anchor < ticket.resolution_due_at:
        ticket.resolution_due_at = reproject(anchor, ticket.resolution_due_at, old, new)

    if (
        ticket.status in FIRST_RESPONSE_STATUSES
        and ticket.first_response_at is None
        and now < ticket.first_response_due_at
    ):
        ticket.first_response_due_at = reproject(now, ticket.first_response_due_at, old, new)

    after = {
        "resolution_due_at": ticket.resolution_due_at,
        "first_response_due_at": ticket.first_response_due_at,
    }
    changed = {key: after[key] for key in before if before[key] != after[key]}
    if not changed:
        return False

    # With the new calendar: the warnings are counted backwards from the
    # deadlines in business time.
    ticket.save(update_fields=[*changed, *refresh_warnings(ticket, new), "updated_at"])
    TicketEvent.objects.create(
        ticket=ticket,
        organization_id=ticket.organization_id,
        actor=None,  # A system action.
        event_type=EventType.SLA_RECALCULATED,
        metadata={
            **{
                key: {"from": before[key].isoformat(), "to": after[key].isoformat()}
                for key in changed
            },
            **context,
        },
    )
    return True
