import datetime

import factory

from apps.sla.models import (
    END_OF_DAY,
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


class AlwaysOpenCalendarFactory(BusinessCalendarFactory):
    """A 24/7 calendar: business time equals wall-clock time.

    The organizations of the test data use it, so tests about tickets get exact
    values without depending on working hours. Tests about business time build
    their own calendars.
    """

    class Meta:
        # The hook only adds slots; the calendar itself needs no second save.
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"24/7 {n}")

    @factory.post_generation
    def always_open(obj, create, extracted, **kwargs):
        if create:
            BusinessHours.objects.bulk_create(
                BusinessHours(calendar=obj, weekday=day, start_time=END_OF_DAY, end_time=END_OF_DAY)
                for day in Weekday
            )


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
