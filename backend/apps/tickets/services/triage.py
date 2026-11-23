"""Triage: staff change a ticket's assignee, priority and category.

Status never changes here (that is transition()'s job), but a priority change
moves the SLA deadlines, so the ticket row is locked as in transition(): a
priority change and a resolution must not interleave, since both read and write
the deadline and the resolution target.
"""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.exceptions import ActionNotAllowed, StateConflict
from apps.organizations.models import Organization
from apps.sla.business_time import add_business_minutes
from apps.sla.models import Priority
from apps.sla.services import load_schedule, resolve_policy
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.services.deadlines import WARNING_FIELDS, refresh_warnings

STAFF = frozenset({Role.AGENT, Role.ADMIN})

# Distinguishes "not given" from None, which for the assignee means "unassign".
UNSET = object()


class TicketClosed(StateConflict):
    code = "ticket_closed"

    def __init__(self):
        super().__init__(
            "La incidencia está cerrada y no se puede modificar.", current_status="closed"
        )


class PriorityLocked(StateConflict):
    """The resolution is already measured: its deadline cannot move any more."""

    code = "priority_locked"

    def __init__(self, status: str):
        super().__init__(
            f"La prioridad no se puede cambiar en estado «{Status(status).label}». "
            "Reabre la incidencia si hace falta.",
            current_status=status,
        )


class StaffOnly(ActionNotAllowed):
    code = "staff_only"

    def __init__(self):
        super().__init__("Solo el personal de soporte puede modificar una incidencia.")


def update_ticket(
    ticket: Ticket,
    actor,
    *,
    assignee=UNSET,
    priority=UNSET,
    category=UNSET,
    now: datetime | None = None,
) -> Ticket:
    """Apply the given changes; omitted fields stay as they are.

    One event per field that actually changes: sending the current value is not
    a change. Returns a fresh instance of the ticket.
    """
    if actor is None or actor.role not in STAFF:
        raise StaffOnly()

    with transaction.atomic():
        locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
        # After the lock, as in transition(): not earlier than what it waited for.
        now = now or timezone.now()
        if locked.status == Status.CLOSED:
            raise TicketClosed()
        changed = []

        new_assignee_id = None if assignee in (UNSET, None) else assignee.pk
        # Compared by id: locked.assignee would load the user just to compare.
        if assignee is not UNSET and new_assignee_id != locked.assignee_id:
            _validate_assignee(assignee)
            _event(
                locked,
                actor,
                EventType.ASSIGNEE_CHANGED,
                _id_or_none(locked.assignee_id),
                _id_or_none(new_assignee_id),
            )
            locked.assignee = assignee
            changed.append("assignee")

        if category is not UNSET and category.pk != locked.category_id:
            if not category.is_active:
                raise ValidationError({"category": "La categoría no está activa."})
            _event(
                locked,
                actor,
                EventType.CATEGORY_CHANGED,
                str(locked.category_id),
                str(category.pk),
            )
            locked.category = category
            changed.append("category")

        if priority is not UNSET and priority != locked.priority:
            if priority not in Priority.values:
                raise ValidationError({"priority": "Prioridad no válida."})
            if locked.status == Status.RESOLVED:
                raise PriorityLocked(locked.status)
            metadata = _move_deadlines(locked, priority, now)
            _event(
                locked,
                actor,
                EventType.PRIORITY_CHANGED,
                locked.priority,
                priority,
                metadata,
            )
            locked.priority = priority
            changed += [
                "priority",
                "sla_policy",
                "resolution_target_minutes",
                "resolution_due_at",
                "first_response_due_at",
                *WARNING_FIELDS,
            ]

        if changed:
            locked.save(update_fields=[*changed, "updated_at"])
    return locked


def _validate_assignee(assignee) -> None:
    if assignee is not None and (assignee.role not in STAFF or not assignee.is_active):
        raise ValidationError(
            {"assignee": "Solo se puede asignar a un agente o administrador activo."}
        )


def _move_deadlines(ticket: Ticket, priority: str, now: datetime) -> dict:
    """Apply the new priority's policy to the deadlines (ADR-0002).

    A deadline that has already been breached never moves: lowering the priority
    must not erase a breach (the same rule as calendar changes). Raising it may
    move a deadline into the past: a more urgent ticket can be overdue already.
    Returns the event metadata: old and new values, and what was kept.
    """
    organization = Organization.objects.select_related("business_calendar").get(
        pk=ticket.organization_id
    )
    schedule = load_schedule(organization.business_calendar)
    policy = resolve_policy(organization, priority)
    before = {
        "resolution_due_at": ticket.resolution_due_at,
        "first_response_due_at": ticket.first_response_due_at,
        "resolution_target_minutes": ticket.resolution_target_minutes,
    }
    kept = []

    # While paused, the clock stopped at sla_paused_at: the deadline was breached
    # only if it had already passed then (the pause will extend it on resuming).
    clock_now = ticket.sla_paused_at or now
    if clock_now < ticket.resolution_due_at:
        # The difference from what was promised, not from the policy's current
        # value: the target is the ticket's own copy (consumed = target - left).
        delta = policy.resolution_minutes - ticket.resolution_target_minutes
        ticket.resolution_due_at = add_business_minutes(ticket.resolution_due_at, delta, schedule)
        ticket.resolution_target_minutes = policy.resolution_minutes
    else:
        kept.append("resolution")

    # The first response clock never pauses: its deadline is exactly creation plus
    # the duration, so it is computed again instead of shifted. Once answered, it
    # is history.
    if ticket.first_response_at is None:
        if now < ticket.first_response_due_at:
            ticket.first_response_due_at = add_business_minutes(
                ticket.created_at, policy.first_response_minutes, schedule
            )
        else:
            kept.append("first_response")

    ticket.sla_policy = policy
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
        "kept_breached": kept,
    }


def _event(ticket, actor, event_type, from_value, to_value, metadata=None) -> None:
    TicketEvent.objects.create(
        ticket=ticket,
        organization_id=ticket.organization_id,
        actor=actor,
        event_type=event_type,
        from_value=from_value,
        to_value=to_value,
        metadata=metadata or {},
    )


def _id_or_none(pk) -> str | None:
    # NULL, never '', is "no value" in ticket_event (CHECK ticket_event_values_not_empty).
    return None if pk is None else str(pk)


def _json(value):
    return value.isoformat() if isinstance(value, datetime) else value
