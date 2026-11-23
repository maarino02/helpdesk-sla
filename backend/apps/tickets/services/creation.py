"""Ticket creation: per-client numbering, SLA deadlines and the first event."""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import Role
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.sla.services import compute_due_dates, load_schedule, resolve_policy
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
    now = now or timezone.now()
    _validate(requester, category, priority)

    with transaction.atomic():
        # Reads first, without locks: the organization row lock taken below lasts
        # until the whole transaction commits (with ATOMIC_REQUESTS, the end of the
        # request), so nothing that can run before it should run while holding it.
        organization = Organization.objects.select_related("business_calendar").get(
            pk=requester.organization_id
        )
        if not organization.is_active:
            raise ValidationError("La organización no está activa.")
        policy = resolve_policy(organization, priority)
        due = compute_due_dates(policy, load_schedule(organization.business_calendar), now)

        number = _next_ticket_number(organization.pk)
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


def _next_ticket_number(organization_id: int) -> int:
    """Reserve the organization's next ticket number.

    SELECT ... FOR UPDATE locks the organization row: a concurrent creation for
    the same organization waits here until this transaction ends, and then reads
    the incremented counter. Without the lock, both would read the same value
    (a lost update) and the second INSERT would violate the unique
    (organization_id, number) constraint. Unlike a sequence, a rollback also
    undoes the increment, so numbers have no gaps.
    """
    number = (
        Organization.objects.select_for_update()
        .values_list("next_ticket_number", flat=True)
        .get(pk=organization_id)
    )
    # A queryset UPDATE of this one column: the row trigger only lets clients and
    # agents change next_ticket_number.
    Organization.objects.filter(pk=organization_id).update(
        next_ticket_number=F("next_ticket_number") + 1
    )
    return number
