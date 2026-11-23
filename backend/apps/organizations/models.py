from django.db import models
from django.db.models import Q
from django.db.models.functions import Now


class Organization(models.Model):
    """A client company. Its users and tickets are isolated from other clients."""

    name = models.CharField(max_length=200)
    # Short code shown in ticket references (e.g. "acme" -> ACME-42). Uniqueness and
    # format are Meta constraints rather than unique=True: with unique=True, Django
    # also creates a varchar_pattern_ops index for LIKE 'prefix%' queries we never run.
    slug = models.SlugField(max_length=50, db_index=False)
    business_calendar = models.ForeignKey(
        "sla.BusinessCalendar",
        # A calendar in use cannot be deleted; it has to be replaced first.
        on_delete=models.PROTECT,
        related_name="organizations",
    )
    # Next per-client ticket number. The ticket service reserves it with
    # SELECT ... FOR UPDATE on this row, so concurrent tickets of the same client
    # never get the same number.
    next_ticket_number = models.IntegerField(db_default=1)
    # Clients are deactivated, never deleted: their tickets and history must remain.
    is_active = models.BooleanField(db_default=True)
    # Defaults live in the database, so they also apply to inserts made outside the
    # ORM. Django renders Now() as STATEMENT_TIMESTAMP(): the start of the current
    # statement. PostgreSQL's now() would be the start of the whole transaction.
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "organization"
        verbose_name = "organización"
        verbose_name_plural = "organizaciones"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="organization_unique_slug"),
            # Lowercase words separated by single hyphens: "acme", "acme-iberia".
            models.CheckConstraint(
                condition=Q(slug__regex=r"^[a-z0-9]+(-[a-z0-9]+)*$"),
                name="organization_slug_format",
            ),
            models.CheckConstraint(
                condition=Q(next_ticket_number__gte=1),
                name="organization_next_ticket_number_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name
