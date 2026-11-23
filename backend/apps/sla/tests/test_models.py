import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import BusinessHours, Holiday, Priority, Weekday
from apps.sla.tests.factories import (
    AlwaysOpenCalendarFactory,
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
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
    # 00:00 as an end means 24:00 (midnight at the end of the day).

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (datetime.time(22), datetime.time(0)),  # night shift until midnight
            (datetime.time(0), datetime.time(0)),  # the whole day
        ],
    )
    def test_slots_can_end_at_midnight(self, start, end):
        slot = BusinessHoursFactory(start_time=start, end_time=end)

        slot.full_clean()  # Model validation agrees with the database.

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (datetime.time(23), datetime.time(0)),  # inside 22:00-24:00
            (datetime.time(21), datetime.time(23)),  # crosses into it
            (datetime.time(0), datetime.time(0)),  # the whole day contains it
        ],
    )
    def test_midnight_end_counts_as_the_latest_time(self, start, end):
        existing = BusinessHoursFactory(start_time=datetime.time(22), end_time=datetime.time(0))

        assert_violates(
            "business_hours_no_overlap",
            BusinessHoursFactory,
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=start,
            end_time=end,
        )

    def test_early_morning_and_night_slots_do_not_overlap(self):
        # 00:00 as a start is the beginning of the day; as an end, its end.
        existing = BusinessHoursFactory(start_time=datetime.time(22), end_time=datetime.time(0))

        BusinessHoursFactory(
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=datetime.time(0),
            end_time=datetime.time(6),
        )

    def test_full_clean_validates_overlap_with_a_midnight_end(self):
        # Validation in Python sends the values as parameters: the end expression
        # repeats it, so its parameters must be repeated too.
        existing = BusinessHoursFactory(start_time=datetime.time(22), end_time=datetime.time(0))
        candidate = BusinessHours(
            calendar=existing.calendar,
            weekday=existing.weekday,
            start_time=datetime.time(23),
            end_time=datetime.time(0),
        )

        with pytest.raises(ValidationError, match="se solapa"):
            candidate.full_clean()

    def test_str_shows_midnight_as_24(self):
        slot = BusinessHoursFactory(
            weekday=Weekday.FRIDAY, start_time=datetime.time(22), end_time=datetime.time(0)
        )

        assert str(slot) == "Viernes 22:00-24:00"

    def test_always_open_calendar(self):
        calendar = AlwaysOpenCalendarFactory()

        slots = calendar.business_hours.values_list("weekday", "start_time", "end_time")
        assert sorted(slots) == [(day, datetime.time(0), datetime.time(0)) for day in range(7)]

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


class TestSlaPolicy:
    def test_only_one_default_policy_per_priority(self):
        # The key case: both rows have organization NULL. A plain UNIQUE would let
        # this through, because NULL is never equal to NULL.
        SlaPolicyFactory(priority=Priority.HIGH)

        assert_violates(
            "sla_policy_unique_organization_priority",
            SlaPolicyFactory,
            priority=Priority.HIGH,
        )

    def test_only_one_policy_per_organization_and_priority(self):
        policy = SlaPolicyFactory(organization=OrganizationFactory(), priority=Priority.HIGH)

        assert_violates(
            "sla_policy_unique_organization_priority",
            SlaPolicyFactory,
            organization=policy.organization,
            priority=Priority.HIGH,
        )

    def test_client_policy_can_override_the_default(self):
        organization = OrganizationFactory()
        SlaPolicyFactory(priority=Priority.HIGH)

        SlaPolicyFactory(organization=organization, priority=Priority.HIGH)
        SlaPolicyFactory(organization=OrganizationFactory(), priority=Priority.HIGH)

    def test_full_clean_reports_duplicate_default_policy(self):
        SlaPolicyFactory(priority=Priority.LOW)
        duplicate = SlaPolicyFactory.build(priority=Priority.LOW)

        with pytest.raises(ValidationError):
            duplicate.full_clean()

    def test_unknown_priority_is_rejected(self):
        assert_violates("sla_policy_priority_valid", SlaPolicyFactory, priority="urgent")

    @pytest.mark.parametrize(
        ("first_response", "resolution"),
        [
            (0, 60),  # no time to respond
            (-30, 60),
            (120, 60),  # resolution before first response
        ],
    )
    def test_invalid_minutes_are_rejected(self, first_response, resolution):
        assert_violates(
            "sla_policy_minutes_valid",
            SlaPolicyFactory,
            first_response_minutes=first_response,
            resolution_minutes=resolution,
        )

    def test_resolution_may_equal_first_response(self):
        SlaPolicyFactory(first_response_minutes=60, resolution_minutes=60)

    def test_organization_with_policies_cannot_be_deleted(self):
        policy = SlaPolicyFactory(organization=OrganizationFactory())

        with pytest.raises(ProtectedError):
            policy.organization.delete()
