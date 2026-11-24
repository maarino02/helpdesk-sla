"""Volume seed: the same plan as replay.py, applied in memory and inserted in batches.

Through the services, each ticket costs ~50 queries, so 200,000 tickets would take
hours. Here the effects of each step are computed in Python, with the same pure
functions the services use (business_time, compute_due_dates, refresh_warnings),
and the rows are written with one INSERT per table and batch.

The step rules are a copy of the services' (creation.py, comments.py,
state_machine.py, triage.py): each method below says which one it mirrors.
test_bulk.py replays the same plan both ways and requires identical rows, so a
change to a service fails that test until this module follows it.

Rows go in with INSERT ... SELECT FROM unnest(<one array per column>): one
statement per batch, whatever its size. COPY would be faster, but PostgreSQL
rejects COPY FROM on a table with row-level security for a role subject to it,
and that includes the owner here (FORCE ROW LEVEL SECURITY). With INSERT, the
policies, foreign keys and CHECK constraints validate every row, as for the
services.
"""

import json

from django.db import connection

from apps.accounts.models import Role
from apps.demo.plan import COMMENT, TRANSITION, UPDATE
from apps.organizations.models import Organization
from apps.sla.business_time import add_business_minutes, business_minutes_between
from apps.sla.services import compute_due_dates
from apps.tickets.models import (
    PAUSED_STATUSES,
    EventType,
    Status,
    Ticket,
    TicketComment,
    TicketEvent,
)
from apps.tickets.services.deadlines import refresh_warnings
from apps.tickets.services.sla_alerts import raise_sla_alerts

# Shared with triage.py on purpose: event values must be formatted identically.
from apps.tickets.services.triage import _id_or_none, _json

BATCH_SIZE = 2000  # tickets per batch, with their events and comments

STAFF = frozenset({Role.AGENT, Role.ADMIN})

TICKET_COLUMNS = [
    "organization_id",
    "number",
    "title",
    "description",
    "category_id",
    "priority",
    "status",
    "requester_id",
    "assignee_id",
    "sla_policy_id",
    "first_response_due_at",
    "resolution_due_at",
    "first_response_warning_at",
    "resolution_warning_at",
    "sla_paused_at",
    "resolution_target_minutes",
    "first_response_at",
    "first_response_business_minutes",
    "resolved_at",
    "resolution_business_minutes",
    "closed_at",
    "created_at",
    "updated_at",
]
EVENT_COLUMNS = [
    "ticket_id",
    "organization_id",
    "actor_id",
    "event_type",
    "from_value",
    "to_value",
    "metadata",
    "created_at",
]
COMMENT_COLUMNS = [
    "ticket_id",
    "organization_id",
    "author_id",
    "body",
    "is_internal",
    "created_at",
]


def bulk_replay(plans, *, schedules, policies, now, progress=None) -> int:
    """Apply and insert the plans; return the number of SLA alerts raised at `now`.

    schedules: organization id -> Schedule. policies: (organization id, priority)
    -> SlaPolicy. progress(done): called after each batch with the tickets so far.
    """
    simulator = _Simulator(schedules, policies)
    done = 0
    batch = []
    for plan in plans:
        batch.append(simulator.ticket(plan))
        if len(batch) == BATCH_SIZE:
            done += _insert_batch(batch)
            batch = []
            if progress:
                progress(done)
    if batch:
        done += _insert_batch(batch)
        if progress:
            progress(done)

    for organization_id, number in simulator.next_numbers.items():
        Organization.objects.filter(pk=organization_id).update(next_ticket_number=number)
    # As in replay(): the alerts due at `now`, once.
    return len(raise_sla_alerts(now))


class _Simulated:
    """A ticket being built, with the rows that will go with it."""

    def __init__(self, ticket: Ticket):
        self.ticket = ticket
        self.events = []  # tuples in EVENT_COLUMNS order, without ticket_id
        self.comments = []  # tuples in COMMENT_COLUMNS order, without ticket_id


class _Simulator:
    def __init__(self, schedules, policies):
        self.schedules = schedules
        self.policies = policies
        organization_ids = {organization_id for organization_id, _ in policies}
        self.next_numbers = dict(
            Organization.objects.filter(pk__in=organization_ids).values_list(
                "pk", "next_ticket_number"
            )
        )

    def ticket(self, plan) -> _Simulated:
        """Mirrors creation.create_ticket."""
        organization_id = plan.organization.pk
        schedule = self.schedules[organization_id]
        policy = self.policies[(organization_id, plan.priority)]
        number = self.next_numbers[organization_id]
        self.next_numbers[organization_id] = number + 1
        due = compute_due_dates(policy, schedule, plan.created_at)
        ticket = Ticket(
            organization_id=organization_id,
            number=number,
            title=plan.title,
            description=plan.description,
            category_id=plan.category.pk,
            priority=plan.priority,
            status=Status.NEW,
            requester_id=plan.requester.pk,
            assignee_id=None,
            sla_policy_id=policy.pk,
            resolution_target_minutes=policy.resolution_minutes,
            first_response_due_at=due.first_response,
            resolution_due_at=due.resolution,
            sla_paused_at=None,
            first_response_at=None,
            first_response_business_minutes=None,
            resolved_at=None,
            resolution_business_minutes=None,
            closed_at=None,
            created_at=plan.created_at,
        )
        refresh_warnings(ticket, schedule)
        simulated = _Simulated(ticket)
        self._event(simulated, plan.requester, EventType.CREATED, None, None, {}, plan.created_at)

        for step in plan.steps:
            if step.kind == COMMENT:
                self._comment(simulated, step.actor, step.body, step.internal, step.at)
            elif step.kind == TRANSITION:
                self._transition(simulated, step.status, step.actor, step.at)
            elif step.kind == UPDATE:
                self._update(simulated, step.actor, step.changes, step.at)
            else:
                raise ValueError(f"Unknown step kind {step.kind!r}.")

        # What replay.LAST_ACTIVITY sets: the last event or comment.
        ticket.updated_at = max(row[-1] for row in [*simulated.events, *simulated.comments])
        return simulated

    def _comment(self, simulated, author, body, internal, at) -> None:
        """Mirrors comments.add_comment (after its validation)."""
        ticket = simulated.ticket
        simulated.comments.append((ticket.organization_id, author.pk, body, internal, at))
        if internal:
            return
        if author.role in STAFF:
            if ticket.first_response_at is None:
                ticket.first_response_at = at
                ticket.first_response_business_minutes = business_minutes_between(
                    ticket.created_at, at, self.schedules[ticket.organization_id]
                )
            if ticket.status == Status.NEW:
                self._transition(simulated, Status.OPEN, author, at)
        elif ticket.status == Status.PENDING:
            self._transition(simulated, Status.OPEN, author, at)

    def _transition(self, simulated, to_status, actor, at) -> None:
        """Mirrors state_machine.transition and _apply_effects (after _check)."""
        ticket = simulated.ticket
        schedule = self.schedules[ticket.organization_id]
        from_status = ticket.status

        if from_status in PAUSED_STATUSES and to_status != Status.CLOSED:
            paused = business_minutes_between(ticket.sla_paused_at, at, schedule)
            ticket.resolution_due_at = add_business_minutes(
                ticket.resolution_due_at, paused, schedule
            )
            ticket.sla_paused_at = None
            refresh_warnings(ticket, schedule)

        if from_status == Status.NEW and ticket.assignee_id is None:
            self._event(simulated, actor, EventType.ASSIGNEE_CHANGED, None, str(actor.pk), {}, at)
            ticket.assignee_id = actor.pk

        if to_status == Status.PENDING:
            ticket.sla_paused_at = at
        elif to_status == Status.RESOLVED:
            left = business_minutes_between(at, ticket.resolution_due_at, schedule)
            ticket.resolved_at = at
            ticket.resolution_business_minutes = ticket.resolution_target_minutes - left
            ticket.sla_paused_at = at
        elif to_status == Status.OPEN and from_status == Status.RESOLVED:
            ticket.resolved_at = None
            ticket.resolution_business_minutes = None
        elif to_status == Status.CLOSED:
            ticket.closed_at = at
            ticket.sla_paused_at = None

        ticket.status = to_status
        self._event(simulated, actor, EventType.STATUS_CHANGED, from_status, to_status, {}, at)

    def _update(self, simulated, actor, changes, at) -> None:
        """Mirrors triage.update_ticket (after its validation), field by field."""
        ticket = simulated.ticket
        if "assignee" in changes:
            assignee = changes["assignee"]
            new_id = None if assignee is None else assignee.pk
            if new_id != ticket.assignee_id:
                self._event(
                    simulated,
                    actor,
                    EventType.ASSIGNEE_CHANGED,
                    _id_or_none(ticket.assignee_id),
                    _id_or_none(new_id),
                    {},
                    at,
                )
                ticket.assignee_id = new_id
        if "category" in changes and changes["category"].pk != ticket.category_id:
            category = changes["category"]
            self._event(
                simulated,
                actor,
                EventType.CATEGORY_CHANGED,
                str(ticket.category_id),
                str(category.pk),
                {},
                at,
            )
            ticket.category_id = category.pk
        if "priority" in changes and changes["priority"] != ticket.priority:
            priority = changes["priority"]
            metadata = self._move_deadlines(ticket, priority, at)
            self._event(
                simulated,
                actor,
                EventType.PRIORITY_CHANGED,
                ticket.priority,
                priority,
                metadata,
                at,
            )
            ticket.priority = priority

    def _move_deadlines(self, ticket, priority, at) -> dict:
        """Mirrors triage._move_deadlines for what the plan does: a priority change
        on an open, answered ticket before its resolution deadline. The other cases
        (paused, not answered yet, deadline breached) are not copied, because
        test_bulk.py could not compare them: they fail instead."""
        if (
            ticket.sla_paused_at is not None
            or ticket.first_response_at is None
            or at >= ticket.resolution_due_at
        ):
            raise NotImplementedError(
                "bulk.py only copies priority changes on open, answered tickets before "
                "the deadline; copy the rest of triage._move_deadlines before planning others."
            )
        schedule = self.schedules[ticket.organization_id]
        policy = self.policies[(ticket.organization_id, priority)]
        before = {
            "resolution_due_at": ticket.resolution_due_at,
            "first_response_due_at": ticket.first_response_due_at,
            "resolution_target_minutes": ticket.resolution_target_minutes,
        }
        delta = policy.resolution_minutes - ticket.resolution_target_minutes
        ticket.resolution_due_at = add_business_minutes(ticket.resolution_due_at, delta, schedule)
        ticket.resolution_target_minutes = policy.resolution_minutes
        ticket.sla_policy_id = policy.pk
        refresh_warnings(ticket, schedule)
        after = {
            "resolution_due_at": ticket.resolution_due_at,
            "first_response_due_at": ticket.first_response_due_at,
            "resolution_target_minutes": ticket.resolution_target_minutes,
        }
        return {
            **{
                key: {"from": _json(before[key]), "to": _json(after[key])}
                for key in before
                if before[key] != after[key]
            },
            "kept_breached": [],
        }

    @staticmethod
    def _event(simulated, actor, event_type, from_value, to_value, metadata, at) -> None:
        simulated.events.append(
            (
                simulated.ticket.organization_id,
                None if actor is None else actor.pk,
                event_type,
                from_value,
                to_value,
                json.dumps(metadata),
                at,
            )
        )


# Writing


def _insert_batch(batch: list[_Simulated]) -> int:
    rows = [tuple(getattr(s.ticket, column) for column in TICKET_COLUMNS) for s in batch]
    returned = _insert(Ticket, TICKET_COLUMNS, rows, returning="id, organization_id, number")
    # Matched by the natural key, not by position: the order of RETURNING rows is
    # not guaranteed.
    ids = {(organization_id, number): pk for pk, organization_id, number in returned}
    events, comments = [], []
    for simulated in batch:
        ticket_id = ids[(simulated.ticket.organization_id, simulated.ticket.number)]
        events += [(ticket_id, *row) for row in simulated.events]
        comments += [(ticket_id, *row) for row in simulated.comments]
    _insert(TicketEvent, EVENT_COLUMNS, events)
    _insert(TicketComment, COMMENT_COLUMNS, comments)
    return len(batch)


def _insert(model, columns: list[str], rows: list[tuple], returning: str = "") -> list[tuple]:
    """INSERT ... SELECT FROM unnest(...): one array parameter per column."""
    if not rows:
        return []
    types = {field.column: field.db_type(connection) for field in model._meta.concrete_fields}
    arrays = ", ".join(f"%s::{types[column]}[]" for column in columns)
    sql = (
        f"INSERT INTO {model._meta.db_table} ({', '.join(columns)}) "  # noqa: S608 (model metadata, not input)
        f"SELECT * FROM unnest({arrays})"
    )
    if returning:
        sql += f" RETURNING {returning}"
    with connection.cursor() as cursor:
        cursor.execute(sql, [list(values) for values in zip(*rows, strict=True)])
        return cursor.fetchall() if returning else []
