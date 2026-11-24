"""The warning instants follow the deadlines in every service that moves them.

The organization has the 24/7 test calendar, so business time is real time:
created at NOW with a first response of 60 minutes (warning 12 minutes before)
and a resolution of 480 (warning 96 minutes before).
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import Priority
from apps.sla.services import load_schedule, lock_and_snapshot
from apps.sla.tests.factories import HolidayFactory, SlaPolicyFactory
from apps.tickets.models import Status, Ticket
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.sla_recalculation import recalculate_deadlines, tickets_using_calendar
from apps.tickets.services.state_machine import transition
from apps.tickets.services.triage import update_ticket
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def minutes(n):
    return NOW + timedelta(minutes=n)


def fresh(ticket):
    return Ticket.objects.get(pk=ticket.pk)


@pytest.fixture
def agent():
    return AgentUserFactory()


@pytest.fixture
def ticket():
    SlaPolicyFactory(priority=Priority.MEDIUM, first_response_minutes=60, resolution_minutes=480)
    SlaPolicyFactory(priority=Priority.HIGH, first_response_minutes=30, resolution_minutes=240)
    return create_ticket(
        requester=ClientUserFactory(organization=OrganizationFactory()),
        title="Avisos",
        description="Los avisos siguen a los plazos.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=NOW,
    )


def test_creation_sets_both_warnings(ticket):
    saved = fresh(ticket)

    assert saved.first_response_warning_at == minutes(60 - 12)
    assert saved.resolution_warning_at == minutes(480 - 96)


def test_resuming_moves_the_resolution_warning_with_the_deadline(ticket, agent):
    transition(ticket, Status.OPEN, agent, now=minutes(10))
    transition(ticket, Status.PENDING, agent, now=minutes(20))

    transition(ticket, Status.OPEN, agent, now=minutes(50))  # Paused 30 minutes.

    saved = fresh(ticket)
    assert saved.resolution_due_at == minutes(480 + 30)
    assert saved.resolution_warning_at == minutes(480 + 30 - 96)


def test_priority_change_recomputes_both_warnings(ticket, agent):
    update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(10))

    saved = fresh(ticket)
    # Target 240: warning 48 minutes before the new deadline.
    assert saved.resolution_due_at == minutes(240)
    assert saved.resolution_warning_at == minutes(240 - 48)
    # First response 30 from creation: warning 6 minutes before.
    assert saved.first_response_warning_at == minutes(30 - 6)


def test_calendar_change_recomputes_the_warnings(ticket):
    # The 24/7 calendar is in Madrid; NOW is 11:00 there on Monday 23. Declaring
    # that Monday a holiday leaves the 480 minutes to count from Tuesday 00:00
    # local: due 08:00 local (07:00 UTC), warning 96 minutes earlier.
    calendar = ticket.organization.business_calendar
    old = lock_and_snapshot(calendar.pk)
    HolidayFactory(calendar=calendar, date=date(2026, 11, 23))

    recalculate_deadlines(
        tickets_using_calendar(calendar.pk), old, load_schedule(calendar), now=NOW
    )

    saved = fresh(ticket)
    assert saved.resolution_due_at == datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
    assert saved.resolution_warning_at == datetime(2026, 11, 24, 5, 24, tzinfo=UTC)
