from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q


def validate_timezone(value: str) -> None:
    """Reject names that are not IANA time zones, such as "CET+1" or "Madrid"."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(
            "%(value)s no es una zona horaria IANA válida.", params={"value": value}
        ) from exc


class BusinessCalendar(models.Model):
    """Contracted support hours. Several organizations can share one calendar."""

    name = models.CharField(max_length=100)
    # IANA name (e.g. "Europe/Madrid"). Business hours are local times in this zone;
    # every timestamp stays in UTC. The database cannot check the name against the
    # IANA list, so validation lives in the model.
    timezone = models.CharField(max_length=64, validators=[validate_timezone])

    class Meta:
        db_table = "business_calendar"
        verbose_name = "calendario laboral"
        verbose_name_plural = "calendarios laborales"

    def __str__(self) -> str:
        return f"{self.name} ({self.timezone})"


class OnFixedDate(Func):
    """Turn a time into a timestamp on an arbitrary fixed date (2000-01-01).

    PostgreSQL has range types for numbers, dates and timestamps but not for times.
    Placing every time on the same date makes tsrange() usable for time slots; the
    date itself is meaningless and never stored.
    """

    template = "(DATE '2000-01-01' + %(expressions)s)"
    output_field = models.DateTimeField()


class TsRange(Func):
    """PostgreSQL tsrange(start, end): half-open [start, end) by default."""

    function = "tsrange"
    output_field = DateTimeRangeField()


class Weekday(models.IntegerChoices):
    # Same numbering as Python's date.weekday(), used by the SLA calculation.
    MONDAY = 0, "Lunes"
    TUESDAY = 1, "Martes"
    WEDNESDAY = 2, "Miércoles"
    THURSDAY = 3, "Jueves"
    FRIDAY = 4, "Viernes"
    SATURDAY = 5, "Sábado"
    SUNDAY = 6, "Domingo"


class BusinessHours(models.Model):
    """One working time slot on a weekday. Several rows per day allow split shifts."""

    calendar = models.ForeignKey(
        BusinessCalendar,
        # Deleting a calendar deletes its slots: they mean nothing on their own.
        on_delete=models.CASCADE,
        related_name="business_hours",
    )
    weekday = models.SmallIntegerField(choices=Weekday)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        db_table = "business_hours"
        verbose_name = "franja laboral"
        verbose_name_plural = "franjas laborales"
        ordering = ["calendar", "weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(weekday__gte=0, weekday__lte=6),
                name="business_hours_weekday_range",
            ),
            # Strictly less: a slot cannot be empty or cross midnight. A night shift
            # is stored as two slots, one on each day.
            models.CheckConstraint(
                condition=Q(start_time__lt=F("end_time")),
                name="business_hours_start_before_end",
            ),
            # Two slots of the same calendar and weekday cannot overlap, or business
            # minutes would be counted twice. A CHECK only sees one row; an exclusion
            # constraint compares each new row with the existing ones through a GiST
            # index, like a UNIQUE where "conflict" means overlap (&&) instead of
            # equality. Ranges are [start, end), so 09-14 and 14-18 do not overlap.
            # btree_gist lets the GiST index compare plain bigint/smallint with =.
            ExclusionConstraint(
                name="business_hours_no_overlap",
                expressions=[
                    ("calendar", RangeOperators.EQUAL),
                    ("weekday", RangeOperators.EQUAL),
                    (
                        TsRange(OnFixedDate("start_time"), OnFixedDate("end_time")),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                violation_error_message=(
                    "La franja se solapa con otra del mismo calendario y día."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class Holiday(models.Model):
    """A non-working day in a calendar, so each client can have regional holidays."""

    calendar = models.ForeignKey(
        BusinessCalendar,
        on_delete=models.CASCADE,
        related_name="holidays",
        # The unique (calendar_id, date) index below starts with calendar_id, so it
        # already serves lookups by calendar. A separate FK index would be redundant.
        db_index=False,
    )
    # A local date in the calendar's time zone, not a UTC timestamp.
    date = models.DateField()
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "holiday"
        verbose_name = "festivo"
        verbose_name_plural = "festivos"
        ordering = ["calendar", "date"]
        constraints = [
            # Also creates the index used to look up a calendar's holidays by date.
            models.UniqueConstraint(
                fields=["calendar", "date"],
                name="holiday_unique_calendar_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date:%Y-%m-%d} {self.name}"


class Priority(models.TextChoices):
    # Stored as varchar + CHECK, not a native ENUM: adding or reordering a value is
    # an ordinary migration instead of ALTER TYPE. Ticket.priority reuses this class.
    LOW = "low", "Baja"
    MEDIUM = "medium", "Media"
    HIGH = "high", "Alta"
    CRITICAL = "critical", "Crítica"


class SlaPolicy(models.Model):
    """Response and resolution targets, in business minutes, for one priority.

    organization = NULL is the default policy; a row with an organization is a
    client-specific agreement that overrides the default for that priority.
    """

    organization = models.ForeignKey(
        # String reference: organizations already depends on sla (business calendar),
        # so importing the model here would be a circular import. The migrations stay
        # acyclic: sla 0001-0002 -> organizations 0001 -> sla 0003.
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sla_policies",
        # The unique (organization_id, priority) index starts with organization_id
        # and already serves lookups by organization.
        db_index=False,
    )
    priority = models.CharField(max_length=10, choices=Priority)
    first_response_minutes = models.IntegerField()
    resolution_minutes = models.IntegerField()

    class Meta:
        db_table = "sla_policy"
        verbose_name = "política de SLA"
        verbose_name_plural = "políticas de SLA"
        ordering = ["organization", "priority"]
        constraints = [
            models.CheckConstraint(
                condition=Q(priority__in=Priority.values),
                name="sla_policy_priority_valid",
            ),
            # A plain UNIQUE treats NULLs as distinct, so it would allow several
            # default policies (organization NULL) for the same priority.
            # NULLS NOT DISTINCT (PostgreSQL 15+) compares NULL as equal to NULL.
            models.UniqueConstraint(
                fields=["organization", "priority"],
                nulls_distinct=False,
                name="sla_policy_unique_organization_priority",
            ),
            models.CheckConstraint(
                condition=Q(
                    first_response_minutes__gt=0,
                    resolution_minutes__gte=F("first_response_minutes"),
                ),
                name="sla_policy_minutes_valid",
            ),
        ]

    def __str__(self) -> str:
        scope = self.organization or "Por defecto"
        return f"{scope} · {self.get_priority_display()}"
