"""Reprojection of deadlines after a calendar change.

The calendar is Monday to Friday, 9:00-18:00 in Madrid. The ticket is created on
Monday 23 November 2026 at 10:00 with a 60-minute first response (due Monday
11:00) and a 960-minute resolution: 8 h on Monday (10:00-18:00) and 8 h on
Tuesday (9:00-17:00), due Tuesday 17:00.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import BusinessHours, Priority, Weekday
from apps.sla.services import load_schedule, lock_and_snapshot
from apps.sla.tests.factories import (
    AlwaysOpenCalendarFactory,
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
)
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.sla_recalculation import (
    recalculate_deadlines,
    tickets_of_organization,
    tickets_using_calendar,
)
from apps.tickets.services.state_machine import transition
from apps.tickets.tests.factories import CategoryFactory, TicketFactory

pytestmark = pytest.mark.django_db

MADRID = ZoneInfo("Europe/Madrid")


def madrid(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=MADRID)


def fresh(ticket):
    return Ticket.objects.get(pk=ticket.pk)


@pytest.fixture
def calendar():
    calendar = BusinessCalendarFactory(timezone="Europe/Madrid")
    for weekday in range(5):
        BusinessHoursFactory(calendar=calendar, weekday=weekday)
    return calendar


@pytest.fixture
def organization(calendar):
    return OrganizationFactory(business_calendar=calendar)


@pytest.fixture
def ticket(organization):
    SlaPolicyFactory(priority=Priority.MEDIUM, first_response_minutes=60, resolution_minutes=960)
    created = create_ticket(
        requester=ClientUserFactory(organization=organization),
        title="Sin VPN",
        description="No conecta.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=madrid(23, 10),
    )
    assert created.resolution_due_at == madrid(24, 17)
    return created


@pytest.fixture
def agent():
    return AgentUserFactory()


def change_calendar(calendar, mutate, now):
    """Snapshot, change, reproject: what the admin will do (step 5)."""
    old = lock_and_snapshot(calendar.pk)
    mutate()
    new = load_schedule(calendar)
    return recalculate_deadlines(
        tickets_using_calendar(calendar.pk), old, new, now=now, context={"calendar_id": calendar.pk}
    )


def tuesday_holiday(calendar):
    return lambda: HolidayFactory(calendar=calendar, date=date(2026, 11, 24))


def shorter_mondays(calendar, end):
    return lambda: BusinessHours.objects.filter(calendar=calendar, weekday=Weekday.MONDAY).update(
        end_time=end
    )


class TestRunningClock:
    def test_holiday_moves_the_resolution_deadline(self, calendar, ticket):
        moved = change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

        # 840 minutes left at 12:00 (6 h Monday + 8 h Tuesday); Tuesday is gone.
        assert moved == 1
        assert fresh(ticket).resolution_due_at == madrid(25, 17)

    def test_first_response_breached_is_not_moved(self, calendar, ticket):
        change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

        assert fresh(ticket).first_response_due_at == madrid(23, 11)

    def test_first_response_is_reprojected_while_it_runs(self, calendar, ticket):
        # At 10:30, Mondays end at 10:45: of the 30 minutes left, 15 fit on Monday.
        change_calendar(calendar, shorter_mondays(calendar, time(10, 45)), now=madrid(23, 10, 30))

        updated = fresh(ticket)
        assert updated.first_response_due_at == madrid(24, 9, 15)
        # 930 minutes left: 15 on Monday, 9 h on Tuesday, 6 h 15 on Wednesday.
        assert updated.resolution_due_at == madrid(25, 15, 15)

    def test_breached_resolution_deadline_never_moves(self, calendar, ticket):
        # On Wednesday, with the deadline (Tuesday 17:00) already breached, Tuesday
        # is declared a holiday after the fact. Measured with the new calendar the
        # deadline would land earlier; the breach must stay as it happened.
        moved = change_calendar(calendar, tuesday_holiday(calendar), now=madrid(25, 10))

        assert moved == 0
        assert fresh(ticket).resolution_due_at == madrid(24, 17)
        assert not TicketEvent.objects.filter(event_type=EventType.SLA_RECALCULATED).exists()

    def test_resolution_minutes_stay_exact_after_a_change(self, calendar, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=madrid(23, 10))
        change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

        resolved = transition(ticket, Status.RESOLVED, agent, now=madrid(25, 12))

        # 8 h on Monday (old and new calendar agree) + 3 h on Wednesday.
        assert resolved.resolution_business_minutes == 660


class TestPausedClock:
    def test_pending_is_measured_from_the_pause(self, calendar, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=madrid(23, 10))
        transition(ticket, Status.PENDING, agent, now=madrid(23, 12))
        # While pending, Mondays shrink to 9:00-13:00: it changes the stretch
        # between the pause and now, which is where the anchor matters.
        change_calendar(calendar, shorter_mondays(calendar, time(13)), now=madrid(23, 15))
        assert fresh(ticket).resolution_due_at == madrid(25, 13)

        resumed = transition(ticket, Status.OPEN, agent, now=madrid(25, 10))

        # 840 minutes were left at the pause; resumed on Wednesday at 10:00, they
        # end on Thursday at 15:00 (8 h Wednesday + 6 h Thursday). Anchored at now
        # instead, it would be Thursday 13:00.
        assert resumed.resolution_due_at == madrid(26, 15)

    def test_pending_without_a_first_response_keeps_that_clock(self, calendar, ticket, agent):
        # Moved to pending without any public reply: the first response clock
        # never pauses, so it is reprojected from now.
        transition(ticket, Status.OPEN, agent, now=madrid(23, 10))
        transition(ticket, Status.PENDING, agent, now=madrid(23, 10, 15))

        change_calendar(calendar, shorter_mondays(calendar, time(10, 45)), now=madrid(23, 10, 30))

        assert fresh(ticket).first_response_due_at == madrid(24, 9, 15)

    def test_resolved_is_measured_from_the_pause(self, calendar, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=madrid(23, 10))
        transition(ticket, Status.RESOLVED, agent, now=madrid(23, 14))
        minutes_before = fresh(ticket).resolution_business_minutes

        change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 16))

        updated = fresh(ticket)
        # 720 minutes were left at 14:00: 4 h Monday, Tuesday gone, 8 h Wednesday.
        assert updated.resolution_due_at == madrid(25, 17)
        assert updated.resolution_business_minutes == minutes_before


class TestScope:
    def test_closed_tickets_are_not_touched(self, calendar, organization):
        closed = TicketFactory(organization=organization, closed=True)
        due = closed.resolution_due_at

        moved = change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

        assert moved == 0
        assert fresh(closed).resolution_due_at == due

    def test_other_calendars_are_not_touched(self, calendar, ticket):
        other = TicketFactory()  # A 24/7 organization.
        due = other.resolution_due_at

        change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

        assert fresh(other).resolution_due_at == due

    def test_organization_switching_calendars(self, organization, ticket):
        old = lock_and_snapshot(organization.business_calendar_id)
        always_open = AlwaysOpenCalendarFactory(timezone="Europe/Madrid")
        organization.business_calendar = always_open
        organization.save()

        recalculate_deadlines(
            tickets_of_organization(organization.pk),
            old,
            load_schedule(always_open),
            now=madrid(23, 12),
        )

        # 840 business minutes left become 14 real hours: Tuesday 02:00.
        assert fresh(ticket).resolution_due_at == madrid(24, 2)

    def test_same_schedule_does_nothing(self, calendar, ticket):
        schedule = load_schedule(calendar)

        with CaptureQueriesContext(connection) as queries:
            moved = recalculate_deadlines(
                tickets_using_calendar(calendar.pk), schedule, schedule, now=madrid(23, 12)
            )

        assert (moved, len(queries)) == (0, 0)


def test_event_records_the_change(calendar, ticket):
    change_calendar(calendar, tuesday_holiday(calendar), now=madrid(23, 12))

    event = TicketEvent.objects.get(ticket=ticket, event_type=EventType.SLA_RECALCULATED)
    assert event.actor is None
    # Stored in UTC, as every timestamp: 17:00 in Madrid is 16:00 UTC in November.
    assert event.metadata == {
        "resolution_due_at": {
            "from": madrid(24, 17).astimezone(UTC).isoformat(),
            "to": madrid(25, 17).astimezone(UTC).isoformat(),
        },
        "calendar_id": calendar.pk,
    }


def test_lock_and_snapshot_takes_the_calendar_lock_first(calendar):
    with CaptureQueriesContext(connection) as queries:
        snapshot = lock_and_snapshot(calendar.pk)

    assert snapshot == load_schedule(calendar)
    # The exclusive advisory lock, before reading anything.
    assert "pg_advisory_xact_lock(" in queries.captured_queries[0]["sql"]


def test_a_change_that_does_not_affect_the_deadline_records_nothing(organization, calendar):
    # A holiday already past: business time until each deadline is the same with
    # both calendars, so no deadline moves, not even by the truncated seconds.
    ticket = TicketFactory(organization=organization)  # Due in about a day, real time.
    due = ticket.resolution_due_at

    moved = change_calendar(
        calendar, lambda: HolidayFactory(calendar=calendar, date=date(2026, 1, 6)), now=None
    )

    assert moved == 0
    assert fresh(ticket).resolution_due_at == due
