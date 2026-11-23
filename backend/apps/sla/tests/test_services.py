from datetime import UTC, date, datetime, time, timedelta

import pytest

from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.business_time import Slot
from apps.sla.models import Priority, Weekday
from apps.sla.services import (
    DueDates,
    MissingSlaPolicy,
    compute_due_dates,
    load_organization_schedule,
    load_schedule,
    resolve_policy,
)
from apps.sla.tests.factories import (
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
)

pytestmark = pytest.mark.django_db


class TestLoadSchedule:
    def test_slots_by_weekday_sorted_by_start(self):
        calendar = BusinessCalendarFactory(timezone="America/Mexico_City")
        for start, end in [(time(15), time(18)), (time(9), time(14))]:
            BusinessHoursFactory(
                calendar=calendar, weekday=Weekday.MONDAY, start_time=start, end_time=end
            )
        BusinessHoursFactory(calendar=calendar, weekday=Weekday.FRIDAY)

        schedule = load_schedule(calendar)

        assert str(schedule.timezone) == "America/Mexico_City"
        assert schedule.weekly_slots[Weekday.MONDAY] == (
            Slot(time(9), time(14)),
            Slot(time(15), time(18)),
        )
        assert schedule.weekly_slots[Weekday.FRIDAY] == (Slot(time(9), time(18)),)
        assert schedule.weekly_slots[Weekday.SUNDAY] == ()
        assert len(schedule.weekly_slots) == 7

    def test_holidays(self):
        calendar = BusinessCalendarFactory()
        HolidayFactory(calendar=calendar, date=date(2026, 12, 25))
        HolidayFactory(date=date(2026, 12, 8))  # Another calendar.

        assert load_schedule(calendar).holidays == frozenset({date(2026, 12, 25)})

    def test_two_queries(self, django_assert_num_queries):
        calendar = BusinessCalendarFactory()
        # Several rows each: the query count must not grow with them.
        for hour in (9, 11, 13):
            BusinessHoursFactory(calendar=calendar, start_time=time(hour), end_time=time(hour + 1))
        HolidayFactory.create_batch(2, calendar=calendar)

        with django_assert_num_queries(2):
            load_schedule(calendar)


class TestResolvePolicy:
    def test_organization_policy_wins_over_default(self):
        organization = OrganizationFactory()
        SlaPolicyFactory(priority=Priority.HIGH)
        own = SlaPolicyFactory(organization=organization, priority=Priority.HIGH)

        assert resolve_policy(organization, Priority.HIGH) == own

    def test_default_policy_when_organization_has_none(self):
        organization = OrganizationFactory()
        default = SlaPolicyFactory(priority=Priority.HIGH)
        SlaPolicyFactory(organization=organization, priority=Priority.LOW)

        assert resolve_policy(organization, Priority.HIGH) == default

    def test_other_organizations_policies_are_never_used(self):
        organization = OrganizationFactory()
        default = SlaPolicyFactory(priority=Priority.HIGH)
        SlaPolicyFactory(organization=OrganizationFactory(), priority=Priority.HIGH)

        assert resolve_policy(organization, Priority.HIGH) == default

    def test_missing_policy(self):
        SlaPolicyFactory(priority=Priority.LOW)

        with pytest.raises(MissingSlaPolicy):
            resolve_policy(OrganizationFactory(), Priority.CRITICAL)

    def test_one_query(self, django_assert_num_queries):
        organization = OrganizationFactory()
        SlaPolicyFactory(priority=Priority.HIGH)
        SlaPolicyFactory(organization=organization, priority=Priority.HIGH)

        with django_assert_num_queries(1):
            resolve_policy(organization, Priority.HIGH)


def test_compute_due_dates():
    calendar = BusinessCalendarFactory()
    policy = SlaPolicyFactory(first_response_minutes=60, resolution_minutes=480)
    start = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)

    due = compute_due_dates(policy, load_schedule(calendar), start)

    # Provisional wall-clock calculation.
    assert due == DueDates(
        first_response=start + timedelta(minutes=60),
        resolution=start + timedelta(minutes=480),
    )


def test_load_organization_schedule(django_assert_num_queries):
    organization = OrganizationFactory(business_calendar__timezone="Atlantic/Canary")
    BusinessHoursFactory(calendar=organization.business_calendar, weekday=Weekday.TUESDAY)

    with django_assert_num_queries(3):  # Calendar, slots, holidays.
        schedule = load_organization_schedule(organization.pk)

    assert str(schedule.timezone) == "Atlantic/Canary"
    assert schedule.weekly_slots[Weekday.TUESDAY] == (Slot(time(9), time(18)),)
