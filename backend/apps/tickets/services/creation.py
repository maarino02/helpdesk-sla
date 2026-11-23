"""Ticket creation: per-client numbering, SLA deadlines and the first event."""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import Role
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.sla.services import (
    compute_due_dates,
    load_calendar_schedule,
    resolve_policy,
    share_calendar_lock,
)
from apps.tickets.models import Category, EventType, Ticket, TicketEvent


def create_ticket(
    *,
    requester,
    title: str,
    description: str,
    category: Category,
    priority: str,
    now: datetime | None = None,
) -> Ticket:
    """Create a ticket for the requester's organization.

    Only clients create tickets, so the organization is the requester's.
    `now` is the creation instant, used for created_at and for the deadlines;
    tests pass it to get exact values.

    Raises ValidationError for invalid input and MissingSlaPolicy if no policy
    covers the priority.
    """
    _validate(requester, category, priority)

    with transaction.atomic():
        # Reads first, without locks: the organization row lock taken below lasts
        # until the whole transaction commits (with ATOMIC_REQUESTS, the end of the
        # request), so nothing that can run before it should run while holding it.
        organization = Organization.objects.get(pk=requester.organization_id)
        if not organization.is_active:
            raise ValidationError("La organización no está activa.")
        policy = resolve_policy(organization, priority)
        # The calendar's shared lock before reading its schedule: a calendar change
        # in progress holds the exclusive one, so this waits and then reads the new
        # schedule; and a change that starts now waits for this ticket to commit,
        # so its recalculation includes it (ADR-0005).
        calendar_id = organization.business_calendar_id
        share_calendar_lock(calendar_id)
        schedule = load_calendar_schedule(calendar_id)

        number, current_calendar_id = _next_ticket_number(organization.pk)
        if current_calendar_id != calendar_id:
            # The organization switched calendars and committed while this ticket
            # waited for the organization lock: use the new calendar.
            share_calendar_lock(current_calendar_id)
            schedule = load_calendar_schedule(current_calendar_id)
        # The instant is taken after the lock: a creation that waited for it gets a
        # later created_at than the one it waited for, so numbers and creation times
        # are in the same order.
        now = now or timezone.now()
        due = compute_due_dates(policy, schedule, now)
        ticket = Ticket.objects.create(
            organization=organization,
            number=number,
            title=title,
            description=description,
            category=category,
            priority=priority,
            requester=requester,
            sla_policy=policy,
            resolution_target_minutes=policy.resolution_minutes,
            first_response_due_at=due.first_response,
            resolution_due_at=due.resolution,
            # Explicit instead of the db_default: the deadlines are computed from
            # this same instant.
            created_at=now,
        )
        TicketEvent.objects.create(
            ticket=ticket,
            organization=organization,
            actor=requester,
            event_type=EventType.CREATED,
        )
    return ticket


def _validate(requester, category: Category, priority: str) -> None:
    errors = {}
    if requester.role != Role.CLIENT or not requester.is_active:
        errors["requester"] = "Solo un cliente activo puede abrir incidencias."
    if not category.is_active:
        errors["category"] = "La categoría no está activa."
    if priority not in Priority.values:
        errors["priority"] = "Prioridad no válida."
    if errors:
        raise ValidationError(errors)


def _next_ticket_number(organization_id: int) -> tuple[int, int]:
    """Reserve the organization's next ticket number.

    Returns it with the organization's calendar as read under the lock.

    SELECT ... FOR UPDATE locks the organization row: a concurrent creation for
    the same organization waits here until this transaction ends, and then reads
    the incremented counter. Without the lock, both would read the same value
    (a lost update) and the second INSERT would violate the unique
    (organization_id, number) constraint. Unlike a sequence, a rollback also
    undoes the increment, so numbers have no gaps.
    """
    number, calendar_id = (
        Organization.objects.select_for_update()
        .values_list("next_ticket_number", "business_calendar_id")
        .get(pk=organization_id)
    )
    # A queryset UPDATE of this one column: the row trigger only lets clients and
    # agents change next_ticket_number.
    Organization.objects.filter(pk=organization_id).update(
        next_ticket_number=F("next_ticket_number") + 1
    )
    return number, calendar_id
