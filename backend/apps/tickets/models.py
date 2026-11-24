from django.conf import settings
from django.db import models
from django.db.models import CharField, F, Func, Q
from django.db.models.functions import Lower, Now
from django.db.models.lookups import Exact

from apps.sla.models import Priority


class Status(models.TextChoices):
    # Allowed transitions live in tickets/services/state_machine.py.
    NEW = "new", "Nuevo"
    OPEN = "open", "Abierto"
    PENDING = "pending", "En espera del cliente"
    RESOLVED = "resolved", "Resuelto"
    CLOSED = "closed", "Cerrado"


# A ticket has a resolution timestamp exactly while it is resolved or closed.
RESOLVED_STATUSES = [Status.RESOLVED, Status.CLOSED]
# The resolution clock is paused in these statuses (ADR-0002).
PAUSED_STATUSES = [Status.PENDING, Status.RESOLVED]


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
    # SELECT ... FOR UPDATE on organization.next_ticket_number.
    number = models.IntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="tickets")
    priority = models.CharField(max_length=10, choices=Priority)
    status = models.CharField(max_length=20, choices=Status, db_default=Status.NEW)
    # Users are deactivated, never deleted: tickets and history keep pointing to them.
    # In the database, the simple FK is replaced by the composite FK
    # (requester_id, organization_id) -> app_user (id, organization_id) created in
    # tickets 0004: the requester must be a client of this ticket's organization.
    # db_constraint=False keeps the ORM relation and the PROTECT check (done by Django
    # in Python) without a second, redundant constraint.
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_tickets",
        db_constraint=False,
    )
    # That the assignee is an agent or admin is checked in the service layer.
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_tickets",
        # Covered by the (assignee_id, status) index, which starts with this column.
        db_index=False,
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
    # When 80 % of each deadline's business time is consumed: the SLA job raises
    # the warning alert from here with plain comparisons and partial indexes, as it
    # does with the deadlines (business time cannot be computed in SQL). Derived
    # data: rewritten with the deadlines by the ticket services.
    first_response_warning_at = models.DateTimeField()
    resolution_warning_at = models.DateTimeField()
    # Start of the current pause of the resolution clock: set exactly while the
    # ticket is pending or resolved (CHECK below). In closed the clock is stopped
    # for good, not paused.
    sla_paused_at = models.DateTimeField(null=True, blank=True)
    # Business minutes the resolution may take, copied from the policy on creation
    # and adjusted on priority changes. A copy, like the price on an order line:
    # editing the policy must not change what open tickets were promised. With it,
    # consumed = target - business minutes left until resolution_due_at.
    resolution_target_minutes = models.IntegerField()

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
        # First proposal, to be validated with EXPLAIN ANALYZE on seed data (phase 8).
        # Django limits index names to 30 characters.
        indexes = [
            # Client ticket list filtered by status.
            models.Index(fields=["organization", "status"], name="ticket_org_status_idx"),
            # Agent work queue and workload per agent.
            models.Index(fields=["assignee", "status"], name="ticket_assignee_status_idx"),
            # Partial indexes for the SLA alert job (phase 6). The planner only uses a
            # partial index when the query's WHERE implies the index predicate, so that
            # query must repeat these exact conditions.
            models.Index(
                fields=["resolution_due_at"],
                condition=Q(status__in=[Status.NEW, Status.OPEN]),  # clock running
                name="ticket_resolution_due_idx",
            ),
            models.Index(
                fields=["first_response_due_at"],
                condition=Q(first_response_at__isnull=True),
                name="ticket_first_resp_due_idx",
            ),
            # The same partial conditions for the warning instants. The job's query
            # for the first response adds a status filter, which does not stop the
            # planner from using this index (its WHERE still implies the predicate).
            models.Index(
                fields=["resolution_warning_at"],
                condition=Q(status__in=[Status.NEW, Status.OPEN]),
                name="ticket_resolution_warn_idx",
            ),
            models.Index(
                fields=["first_response_warning_at"],
                condition=Q(first_response_at__isnull=True),
                name="ticket_first_resp_warn_idx",
            ),
            # Auto-close job: resolved tickets in (resolved_at, id) order, the key
            # it pages through in batches. Partial: resolved is a small, transient
            # share of the table, while closed tickets accumulate forever.
            models.Index(
                fields=["resolved_at", "id"],
                condition=Q(status=Status.RESOLVED),
                name="ticket_resolved_at_idx",
            ),
            # Monthly dashboard aggregations and the default -created_at ordering:
            # a B-tree returns rows already sorted, so ORDER BY ... LIMIT needs no sort.
            models.Index(fields=["created_at"], name="ticket_created_at_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="ticket_unique_organization_number",
            ),
            # Target of the composite FKs (ticket_id, organization_id) of comments,
            # events and alerts, which keep their denormalized organization_id in
            # sync with the ticket's.
            models.UniqueConstraint(
                fields=["id", "organization"],
                name="ticket_unique_id_organization",
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
            # A warning never comes after its deadline.
            models.CheckConstraint(
                condition=Q(first_response_warning_at__lte=F("first_response_due_at")),
                name="ticket_first_resp_warn_before_due",
            ),
            models.CheckConstraint(
                condition=Q(resolution_warning_at__lte=F("resolution_due_at")),
                name="ticket_resolution_warn_before_due",
            ),
            # The resolution clock is paused exactly in pending and resolved.
            models.CheckConstraint(
                condition=(
                    Q(status__in=PAUSED_STATUSES, sla_paused_at__isnull=False)
                    | (~Q(status__in=PAUSED_STATUSES) & Q(sla_paused_at__isnull=True))
                ),
                name="ticket_sla_paused_at_matches_status",
            ),
            models.CheckConstraint(
                condition=Q(resolution_target_minutes__gt=0),
                name="ticket_resolution_target_positive",
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
        return self.reference

    @property
    def reference(self) -> str:
        """The reference clients see, e.g. ACME-42. Needs the organization loaded."""
        return f"{self.organization.slug.upper()}-{self.number}"


# Comments and events copy the ticket's organization_id so that Row-Level Security
# policies can filter them without a join (phase 3). In the database, the composite
# FK (ticket_id, organization_id) -> ticket (id, organization_id) created in
# tickets 0006 keeps that copy in sync and replaces the simple FKs: the Django FKs
# below use db_constraint=False and only keep the ORM relation and the PROTECT check.


class TicketComment(models.Model):
    """A message in the ticket conversation. Comments are never edited."""

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="comments",
        db_constraint=False,
        # Covered by the (ticket_id, created_at) index.
        db_index=False,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        # No reverse accessor: comments are always reached through their ticket.
        related_name="+",
        db_constraint=False,
        db_index=False,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_comments",
    )
    body = models.TextField()
    # Agent-only note: hidden from clients and does not count as the first response.
    is_internal = models.BooleanField(db_default=False)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "ticket_comment"
        verbose_name = "comentario"
        verbose_name_plural = "comentarios"
        # id breaks ties between comments created at the same instant.
        ordering = ["created_at", "id"]
        indexes = [
            # Ordered comment thread of a ticket.
            models.Index(fields=["ticket", "created_at"], name="ticket_comment_ticket_idx"),
        ]
        constraints = [
            # At least one non-whitespace character. NOT NULL alone accepts "" and
            # "   ", and trim() would only strip spaces, not newlines or tabs.
            models.CheckConstraint(
                condition=Q(body__regex=r"\S"),
                name="ticket_comment_body_not_blank",
                violation_error_message="El comentario no puede estar vacío.",
            ),
        ]

    def __str__(self) -> str:
        return f"Comentario {self.pk} en {self.ticket}"


class EventType(models.TextChoices):
    CREATED = "created", "Creación"
    STATUS_CHANGED = "status_changed", "Cambio de estado"
    ASSIGNEE_CHANGED = "assignee_changed", "Cambio de asignación"
    PRIORITY_CHANGED = "priority_changed", "Cambio de prioridad"
    CATEGORY_CHANGED = "category_changed", "Cambio de categoría"
    # System event after a calendar change (ADR-0002); actor is NULL.
    SLA_RECALCULATED = "sla_recalculated", "Recálculo del SLA"


class TicketEvent(models.Model):
    """Append-only ticket history.

    The application role will have UPDATE and DELETE revoked on this table (phase 3).
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="events",
        db_constraint=False,
        # Covered by the (ticket_id, created_at) index.
        db_index=False,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        db_constraint=False,
        db_index=False,
    )
    # NULL = action performed by the system (auto-close, SLA recalculation).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ticket_events",
    )
    event_type = models.CharField(max_length=30, choices=EventType)
    # Old and new value of the changed field (status, assignee id, priority).
    # NULL means "no value" (e.g. from_value of a first assignment). Django's
    # convention is '' instead of NULL for text, to avoid two empty values; here the
    # ticket_event_values_not_empty CHECK forbids '' so NULL is the only one.
    from_value = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    to_value = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    # Extra details, e.g. old and new deadlines in sla_recalculated.
    metadata = models.JSONField(db_default={})
    # The services set it to the instant of the change they record, the same one
    # stored on the ticket; the default only covers rows inserted by hand.
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "ticket_event"
        verbose_name = "evento"
        verbose_name_plural = "eventos"
        ordering = ["created_at", "id"]
        indexes = [
            # Ticket history in order.
            models.Index(fields=["ticket", "created_at"], name="ticket_event_ticket_idx"),
            # One client's history by date: the daily backlog of the dashboard reads
            # it all (docs/analytics.md#performance).
            models.Index(
                fields=["organization", "created_at"], name="ticket_event_org_created_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(event_type__in=EventType.values),
                name="ticket_event_type_valid",
            ),
            # NULL passes (a CHECK only rejects FALSE): only '' is forbidden.
            models.CheckConstraint(
                condition=~Q(from_value="") & ~Q(to_value=""),
                name="ticket_event_values_not_empty",
            ),
            # Always a JSON object, never a list, string or number at the top level,
            # so readers can always do metadata->>'key'.
            models.CheckConstraint(
                condition=Q(
                    Exact(
                        Func("metadata", function="jsonb_typeof", output_field=CharField()),
                        "object",
                    )
                ),
                name="ticket_event_metadata_is_object",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} en {self.ticket}"


class AlertKind(models.TextChoices):
    FIRST_RESPONSE = "first_response", "Primera respuesta"
    RESOLUTION = "resolution", "Resolución"


class AlertLevel(models.TextChoices):
    WARNING = "warning", "Aviso"  # 80% of the deadline consumed
    BREACHED = "breached", "Incumplido"  # deadline expired


class SlaAlert(models.Model):
    """An SLA alert already raised for a ticket.

    Created by the SLA job (phase 6) with INSERT ... ON CONFLICT DO NOTHING on the
    unique (ticket, kind, level) key: running the job twice never duplicates an alert
    or its notification. Consequence: at most one warning per ticket and kind, even if
    the deadline is later extended (pause, priority or calendar change).
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="sla_alerts",
        # Same pattern as comments and events: composite FK in tickets 0008.
        db_constraint=False,
        # Covered by the unique (ticket_id, kind, level) index.
        db_index=False,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        db_constraint=False,
        db_index=False,
    )
    kind = models.CharField(max_length=20, choices=AlertKind)
    level = models.CharField(max_length=10, choices=AlertLevel)
    # Copy of the ticket deadline the alert refers to. The ticket's deadline may move
    # afterwards (pauses, priority or calendar changes), so the alert keeps the value
    # that triggered it.
    due_at = models.DateTimeField()
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "sla_alert"
        verbose_name = "alerta de SLA"
        verbose_name_plural = "alertas de SLA"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "kind", "level"],
                name="sla_alert_unique_ticket_kind_level",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=AlertKind.values),
                name="sla_alert_kind_valid",
            ),
            models.CheckConstraint(
                condition=Q(level__in=AlertLevel.values),
                name="sla_alert_level_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_level_display()} de {self.get_kind_display().lower()} en {self.ticket}"
