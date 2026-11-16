from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


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
