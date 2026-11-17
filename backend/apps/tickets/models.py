from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Now

from apps.sla.models import Priority


class Status(models.TextChoices):
    # Allowed transitions live in tickets/services/state_machine.py (phase 4).
    NEW = "new", "Nuevo"
    OPEN = "open", "Abierto"
    PENDING = "pending", "En espera del cliente"
    RESOLVED = "resolved", "Resuelto"
    CLOSED = "closed", "Cerrado"


# A ticket has a resolution timestamp exactly while it is resolved or closed.
RESOLVED_STATUSES = [Status.RESOLVED, Status.CLOSED]


class Category(models.Model):
    """Global ticket classification, managed by admins."""

    name = models.CharField(max_length=100)
    # Categories in use are deactivated, never deleted (tickets keep their category).
    is_active = models.BooleanField(db_default=True)

    class Meta:
        db_table = "category"
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["name"]
        constraints = [
            # Unique index on lower(name): "Hardware" and "hardware" cannot coexist,
            # but the name is stored as typed because it is displayed. Unlike email,
            # nothing looks categories up by name, so queries never need lower().
            models.UniqueConstraint(
                Lower("name"),
                name="category_unique_name_ci",
                violation_error_message="Ya existe una categoría con ese nombre.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Ticket(models.Model):
    """An incident reported by a client user.

    SLA deadlines are computed in Python when the ticket is created and stored here
    (ADR-0002). Status changes only through the state machine service (ADR-0003).
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="tickets",
        # The unique (organization_id, number) index starts with organization_id and
        # already serves lookups by organization.
        db_index=False,
    )
    # Per-client number shown as e.g. ACME-42. Assigned by the ticket service with
    # SELECT ... FOR UPDATE on organization.next_ticket_number (phase 4).
    number = models.IntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="tickets")
    priority = models.CharField(max_length=10, choices=Priority)
    status = models.CharField(max_length=20, choices=Status, db_default=Status.NEW)
    # Users are deactivated, never deleted: tickets and history keep pointing to them.
    # That the requester is a client of this organization is enforced in step 7 with a
    # composite FK (requester_id, organization_id) -> app_user (id, organization_id).
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_tickets",
    )
    # That the assignee is an agent or admin is checked in the service layer.
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    sla_policy = models.ForeignKey(
        "sla.SlaPolicy",
        on_delete=models.PROTECT,
        related_name="tickets",
    )

    # SLA deadlines, always present: computed in the same transaction that creates
    # the ticket.
    first_response_due_at = models.DateTimeField()
    resolution_due_at = models.DateTimeField()
    # Start of the current pause of the resolution clock (pending, resolved).
    sla_paused_at = models.DateTimeField(null=True, blank=True)

    # Key moments, with the business minutes consumed until each of them (the main
    # dashboard metric). The CHECKs below keep each pair in sync.
    first_response_at = models.DateTimeField(null=True, blank=True)
    first_response_business_minutes = models.IntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_business_minutes = models.IntegerField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket"
        verbose_name = "incidencia"
        verbose_name_plural = "incidencias"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="ticket_unique_organization_number",
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1),
                name="ticket_number_positive",
            ),
            models.CheckConstraint(
                condition=Q(priority__in=Priority.values),
                name="ticket_priority_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=Status.values),
                name="ticket_status_valid",
            ),
            # Equivalences, not just implications: a closed ticket must have
            # closed_at, and a ticket that is not closed must not have it.
            models.CheckConstraint(
                condition=(
                    Q(status=Status.CLOSED, closed_at__isnull=False)
                    | (~Q(status=Status.CLOSED) & Q(closed_at__isnull=True))
                ),
                name="ticket_closed_at_matches_status",
            ),
            # resolved -> open (reopen) clears resolved_at; closed keeps it.
            models.CheckConstraint(
                condition=(
                    Q(status__in=RESOLVED_STATUSES, resolved_at__isnull=False)
                    | (~Q(status__in=RESOLVED_STATUSES) & Q(resolved_at__isnull=True))
                ),
                name="ticket_resolved_at_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    Q(first_response_at__isnull=True, first_response_business_minutes__isnull=True)
                    | Q(
                        first_response_at__isnull=False,
                        first_response_business_minutes__isnull=False,
                    )
                ),
                name="ticket_first_response_minutes_with_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    Q(resolved_at__isnull=True, resolution_business_minutes__isnull=True)
                    | Q(resolved_at__isnull=False, resolution_business_minutes__isnull=False)
                ),
                name="ticket_resolution_minutes_with_timestamp",
            ),
            # A CHECK passes when its condition is NULL (unknown), so NULL minutes are
            # accepted here; the two CHECKs above decide when they may be NULL.
            models.CheckConstraint(
                condition=Q(
                    first_response_business_minutes__gte=0,
                    resolution_business_minutes__gte=0,
                ),
                name="ticket_business_minutes_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization.slug.upper()}-{self.number}"
