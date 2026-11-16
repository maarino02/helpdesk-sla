import datetime

import factory

from apps.sla.models import (
    BusinessCalendar,
    BusinessHours,
    Holiday,
    Priority,
    SlaPolicy,
    Weekday,
)


class BusinessCalendarFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BusinessCalendar

    name = factory.Sequence(lambda n: f"Calendar {n}")
    timezone = "Europe/Madrid"


class BusinessHoursFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BusinessHours

    calendar = factory.SubFactory(BusinessCalendarFactory)
    weekday = Weekday.MONDAY
    start_time = datetime.time(9, 0)
    end_time = datetime.time(18, 0)


class HolidayFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Holiday

    calendar = factory.SubFactory(BusinessCalendarFactory)
    date = factory.Sequence(lambda n: datetime.date(2026, 1, 1) + datetime.timedelta(days=n))
    name = factory.Sequence(lambda n: f"Holiday {n}")


class SlaPolicyFactory(factory.django.DjangoModelFactory):
    """Default policy (no organization) unless one is passed."""

    class Meta:
        model = SlaPolicy

    organization = None
    priority = Priority.MEDIUM
    first_response_minutes = 240
    resolution_minutes = 1440
