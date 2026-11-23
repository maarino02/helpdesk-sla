"""The ticket state machine (ADR-0003, docs/state-machine.md).

transition() is the only code that changes ticket.status. The API, the comment
service and the background jobs all go through it, so every status change gets
the same checks, SLA effects and history event.
"""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.exceptions import ActionNotAllowed, DomainError, StateConflict
from apps.sla.business_time import add_business_minutes, business_minutes_between
from apps.sla.services import load_organization_schedule
from apps.tickets.models import PAUSED_STATUSES, EventType, Status, Ticket, TicketEvent

# Background jobs act with no user (actor=None).
SYSTEM = "system"

CLIENT = frozenset({Role.CLIENT})
STAFF = frozenset({Role.AGENT, Role.ADMIN})

# (from, to) -> roles allowed to perform it. A pair missing here does not exist.
# Keep in sync with docs/state-machine.md and the parametrized tests.
TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    (Status.NEW, Status.OPEN): STAFF,
    (Status.OPEN, Status.PENDING): STAFF,
    (Status.PENDING, Status.OPEN): CLIENT | STAFF,
    (Status.OPEN, Status.RESOLVED): STAFF,
    (Status.PENDING, Status.RESOLVED): STAFF,
    (Status.RESOLVED, Status.OPEN): CLIENT | STAFF,
    (Status.RESOLVED, Status.CLOSED): CLIENT | {SYSTEM},
}


class TransitionError(DomainError):
    """Base class: the transition was rejected and nothing was changed."""

    def __init__(self, detail: str, from_status: str, to_status: str, **extra):
        super().__init__(detail, **extra)
        self.from_status = from_status
        self.to_status = to_status


class TransitionNotDefined(TransitionError, StateConflict):
    """No such transition from the ticket's current status (HTTP 409).

    The response carries the current status: the client usually acted on a
    stale view of the ticket and can refresh it without another request.
    """

    code = "transition_not_defined"

    def __init__(self, from_status: str, to_status: str):
        super().__init__(
            f"La incidencia está en estado «{Status(from_status).label}» "
            f"y no puede pasar a «{Status(to_status).label}».",
            from_status,
            to_status,
            current_status=from_status,
        )


class TransitionNotAllowed(TransitionError, ActionNotAllowed):
    """The transition exists, but not for this actor (HTTP 403)."""

    code = "transition_not_allowed"

    def __init__(self, from_status: str, to_status: str):
        super().__init__(
            "No tienes permiso para pasar la incidencia de "
            f"«{Status(from_status).label}» a «{Status(to_status).label}».",
            from_status,
            to_status,
        )


def transition(ticket: Ticket, to_status: str, actor, *, now: datetime | None = None) -> Ticket:
    """Move a ticket to `to_status` on behalf of `actor` (None: the system).

    Returns the updated ticket, a fresh instance: the one passed in may be stale
    and is not modified.
    """
    if to_status not in Status.values:
        # Callers validate input; an unknown status here is a bug, not a conflict.
        raise ValueError(f"Unknown status {to_status!r}.")

    with transaction.atomic():
        # The row lock serializes concurrent transitions of this ticket. The status
        # that counts is the one read after getting the lock: a competing
        # transition may have committed while this one waited. The instant too:
        # taken before waiting, it could be earlier than the change waited for.
        locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
        now = now or timezone.now()
        from_status = locked.status
        _check(locked, from_status, to_status, actor)

        changed = _apply_effects(locked, from_status, to_status, actor, now)
        locked.status = to_status
        locked.save(update_fields=[*changed, "status", "updated_at"])

        TicketEvent.objects.create(
            ticket=locked,
            organization_id=locked.organization_id,
            actor=actor,
            event_type=EventType.STATUS_CHANGED,
            from_value=from_status,
            to_value=to_status,
        )
    return locked


def _check(ticket: Ticket, from_status: str, to_status: str, actor) -> None:
    allowed = TRANSITIONS.get((from_status, to_status))
    if allowed is None:
        raise TransitionNotDefined(from_status, to_status)
    role = SYSTEM if actor is None else actor.role
    # The organization check is defense in depth: the API only shows clients
    # their organization's tickets.
    if role not in allowed or (
        role == Role.CLIENT and actor.organization_id != ticket.organization_id
    ):
        raise TransitionNotAllowed(from_status, to_status)


def _apply_effects(ticket: Ticket, from_status: str, to_status: str, actor, now) -> list[str]:
    """Timestamps, SLA clock and assignment. Returns the fields it changed.

    The resolution clock runs in new and open and is paused in pending and
    resolved (sla_paused_at holds the start of the pause). The deadline already
    includes every pause, so the business minutes consumed are always
    target - business minutes left until the deadline.
    """
    changed = []
    resumes = from_status in PAUSED_STATUSES and to_status != Status.CLOSED
    # Loaded once, and only by the transitions that measure business time.
    schedule = (
        load_organization_schedule(ticket.organization_id)
        if resumes or to_status == Status.RESOLVED
        else None
    )

    if resumes:
        # Leaving a pause: the deadline moves forward by the business minutes the
        # clock was paused. Not on closing: the clock stops for good.
        paused = business_minutes_between(ticket.sla_paused_at, now, schedule)
        ticket.resolution_due_at = add_business_minutes(ticket.resolution_due_at, paused, schedule)
        ticket.sla_paused_at = None
        changed += ["resolution_due_at", "sla_paused_at"]

    if from_status == Status.NEW and ticket.assignee_id is None:
        # The agent who takes an unassigned ticket becomes its assignee. new -> open
        # is the only way out of new, and only staff may do it (checked above).
        _record_assignment(ticket, actor)
        changed.append("assignee")

    if to_status == Status.PENDING:
        ticket.sla_paused_at = now
        changed.append("sla_paused_at")

    elif to_status == Status.RESOLVED:
        left = business_minutes_between(now, ticket.resolution_due_at, schedule)
        ticket.resolved_at = now
        ticket.resolution_business_minutes = ticket.resolution_target_minutes - left
        ticket.sla_paused_at = now
        changed += ["resolved_at", "resolution_business_minutes", "sla_paused_at"]

    elif to_status == Status.OPEN and from_status == Status.RESOLVED:
        # Reopened: the resolution did not hold.
        ticket.resolved_at = None
        ticket.resolution_business_minutes = None
        changed += ["resolved_at", "resolution_business_minutes"]

    elif to_status == Status.CLOSED:
        ticket.closed_at = now
        ticket.sla_paused_at = None
        changed += ["closed_at", "sla_paused_at"]

    return changed


def _record_assignment(ticket: Ticket, assignee) -> None:
    TicketEvent.objects.create(
        ticket=ticket,
        organization_id=ticket.organization_id,
        actor=assignee,
        event_type=EventType.ASSIGNEE_CHANGED,
        from_value=None,
        to_value=str(assignee.pk),
    )
    ticket.assignee = assignee
