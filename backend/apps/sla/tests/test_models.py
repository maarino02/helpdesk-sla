import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.sla.models import BusinessHours, Holiday, Weekday
from apps.sla.tests.factories import (
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
)

pytestmark = pytest.mark.django_db


# Constraint tests save through the ORM without full_clean(), so the row reaches
# PostgreSQL and the database itself must reject it. Matching the constraint name
# proves which rule fired. transaction.atomic() rolls back to a savepoint, so the
# test transaction stays usable after the error.
def assert_violates(constraint_name, factory, **kwargs):
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


class TestBusinessCalendar:
    def test_accepts_iana_timezone(self):
        calendar = BusinessCalendarFactory.build(timezone="America/Mexico_City")

        calendar.full_clean()

    @pytest.mark.parametrize("timezone", ["Madrid", "CET+1", "Europe/Atlantis", "../etc"])
    def test_rejects_unknown_timezone(self, timezone):
        calendar = BusinessCalendarFactory.build(timezone=timezone)

        with pytest.raises(ValidationError) as excinfo:
            calendar.full_clean()
        assert "timezone" in excinfo.value.message_dict


class TestBusinessHours:
    def test_allows_split_shift_on_the_same_day(self):
        calendar = BusinessCalendarFactory()
        BusinessHoursFactory(
            calendar=calendar, start_time=datetime.time(9), end_time=datetime.time(14)
        )
        BusinessHoursFactory(
            calendar=calendar, start_time=datetime.time(15), end_time=datetime.time(18)
        )

        assert calendar.business_hours.count() == 2

    @pytest.mark.parametrize("weekday", [-1, 7])
    def test_weekday_out_of_range_is_rejected(self, weekday):
        assert_violates("business_hours_weekday_range", BusinessHoursFactory, weekday=weekday)

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (datetime.time(18), datetime.time(9)),  # crosses midnight
            (datetime.time(9), datetime.time(9)),  # empty slot
        ],
    )
    def test_start_must_be_before_end(self, start, end):
        assert_violates(
            "business_hours_start_before_end",
            BusinessHoursFactory,
            start_time=start,
            end_time=end,
        )

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (datetime.time(12), datetime.time(16)),  # partial overlap
            (datetime.time(10), datetime.time(12)),  # inside the existing slot
            (datetime.time(8), datetime.time(15)),  # contains the existing slot
            (datetime.time(9), datetime.time(14)),  # identical
        ],
    )
    def test_overlapping_slots_are_rejected(self, start, end):
        existing = BusinessHoursFactory(start_time=datetime.time(9), end_time=datetime.time(14))

        assert_violates(
            "business_hours_no_overlap",
            BusinessHoursFactory,
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=start,
            end_time=end,
        )

    def test_adjacent_slots_do_not_overlap(self):
        # Ranges are half-open [start, end): 14:00 ends one slot and starts the next.
        existing = BusinessHoursFactory(start_time=datetime.time(9), end_time=datetime.time(14))

        BusinessHoursFactory(
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=datetime.time(14),
            end_time=datetime.time(18),
        )

    def test_same_slot_is_allowed_on_another_weekday_or_calendar(self):
        existing = BusinessHoursFactory(weekday=Weekday.MONDAY)

        BusinessHoursFactory(calendar=existing.calendar, weekday=Weekday.TUESDAY)
        BusinessHoursFactory(weekday=Weekday.MONDAY)  # new calendar

        assert BusinessHours.objects.count() == 3

    def test_full_clean_reports_overlap_in_spanish(self):
        # full_clean() runs the same check in Python with a query, so forms and the
        # admin show a readable message instead of a database error.
        existing = BusinessHoursFactory(start_time=datetime.time(9), end_time=datetime.time(14))
        overlapping = BusinessHoursFactory.build(
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=datetime.time(12),
            end_time=datetime.time(16),
        )

        with pytest.raises(ValidationError, match="se solapa"):
            overlapping.full_clean()


class TestHoliday:
    def test_same_date_is_rejected_in_the_same_calendar(self):
        holiday = HolidayFactory()

        assert_violates(
            "holiday_unique_calendar_date",
            HolidayFactory,
            calendar=holiday.calendar,
            date=holiday.date,
        )

    def test_same_date_is_allowed_in_another_calendar(self):
        holiday = HolidayFactory()

        HolidayFactory(date=holiday.date)

    def test_deleting_a_calendar_deletes_its_hours_and_holidays(self):
        calendar = BusinessCalendarFactory()
        BusinessHoursFactory(calendar=calendar)
        HolidayFactory(calendar=calendar)

        calendar.delete()

        assert not BusinessHours.objects.exists()
        assert not Holiday.objects.exists()
