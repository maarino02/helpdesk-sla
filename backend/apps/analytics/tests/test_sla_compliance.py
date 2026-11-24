"""GET /api/analytics/sla-compliance/: sql/sla_compliance.sql on a hand-built dataset.

Every expected figure is worked out by hand from the tickets below. Instants are
written in Madrid time, the provider's zone (ANALYTICS_TIME_ZONE).
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import Priority
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("analytics:sla-compliance")
MADRID = ZoneInfo("Europe/Madrid")
MARCH = {"start": "2026-03-01", "end": "2026-03-31"}


def madrid(month, day, hour, minute=0):
    return dt.datetime(2026, month, day, hour, minute, tzinfo=MADRID)


def answered(answered_at, due_at, **kwargs):
    """A ticket with its first response, still open."""
    return TicketFactory(
        status="open",
        first_response_at=answered_at,
        first_response_business_minutes=10,
        first_response_due_at=due_at,
        **kwargs,
    )


def resolved(resolved_at, due_at, **kwargs):
    """A closed ticket resolved at resolved_at (answered long before)."""
    return TicketFactory(
        closed=True,
        resolved_at=resolved_at,
        resolution_due_at=due_at,
        closed_at=resolved_at + dt.timedelta(days=1),
        first_response_at=madrid(1, 5, 10),
        first_response_business_minutes=10,
        first_response_due_at=madrid(1, 5, 12),
        **kwargs,
    )


def get(user, **params):
    api = APIClient()
    api.force_authenticate(user)
    response = api.get(URL, params)
    assert response.status_code == 200, response.json()
    return response.json()


def row(body, month, priority):
    [found] = [r for r in body["results"] if r["month"] == month and r["priority"] == priority]
    return found


def test_outcomes_met_and_breached_in_the_month():
    answered(madrid(3, 2, 9), madrid(3, 2, 10))  # met
    answered(madrid(3, 3, 9), madrid(3, 3, 10))  # met
    answered(madrid(3, 4, 11), madrid(3, 4, 10))  # breached
    resolved(madrid(3, 10, 9), madrid(3, 10, 12))  # met
    resolved(madrid(3, 11, 15), madrid(3, 11, 12))  # breached

    medium = row(get(AgentUserFactory(), **MARCH), "2026-03-01", Priority.MEDIUM)

    assert medium == {
        "month": "2026-03-01",
        "priority": "medium",
        "first_responses": 3,
        "first_responses_met": 2,
        "first_response_rate": 66.7,
        "resolutions": 2,
        "resolutions_met": 1,
        "resolution_rate": 50.0,
    }


def test_the_all_row_adds_up_the_priorities():
    answered(madrid(3, 2, 9), madrid(3, 2, 10), priority=Priority.HIGH)  # met
    answered(madrid(3, 2, 9), madrid(3, 2, 8), priority=Priority.LOW)  # breached
    answered(madrid(3, 2, 9), madrid(3, 2, 10), priority=Priority.LOW)  # met

    body = get(AgentUserFactory(), **MARCH)

    # Ending on the 31st, the exclusive end is 1 April at midnight: no April row.
    assert len(body["results"]) == len(Priority.values) + 1
    assert row(body, "2026-03-01", "all")["first_responses"] == 3
    assert row(body, "2026-03-01", "all")["first_responses_met"] == 2
    assert row(body, "2026-03-01", Priority.LOW)["first_response_rate"] == 50.0


def test_an_outcome_at_the_deadline_is_a_breach():
    # As the alert job judges it: a deadline equal to now is breached.
    answered(madrid(3, 2, 10), madrid(3, 2, 10))

    assert row(get(AgentUserFactory(), **MARCH), "2026-03-01", "all")["first_responses_met"] == 0


def test_resolving_after_hours_past_the_deadline_is_a_breach():
    # Due at 18:00, resolved at 20:00: no business minutes after 18:00, so the
    # business minutes equal the target. The instant decides, not the minutes.
    ticket = resolved(madrid(3, 10, 20), madrid(3, 10, 18))
    ticket.resolution_business_minutes = ticket.resolution_target_minutes
    ticket.save()

    assert row(get(AgentUserFactory(), **MARCH), "2026-03-01", "all")["resolutions_met"] == 0


def test_months_are_those_of_the_provider_time_zone():
    # 31 March 22:30 UTC is already 1 April 00:30 in Madrid.
    utc = dt.datetime(2026, 3, 31, 22, 30, tzinfo=dt.UTC)
    answered(utc, utc + dt.timedelta(hours=1))

    body = get(AgentUserFactory(), start="2026-03-01", end="2026-04-30")

    assert row(body, "2026-03-01", "all")["first_responses"] == 0
    assert row(body, "2026-04-01", "all")["first_responses"] == 1


def test_each_outcome_counts_in_its_own_month():
    # Answered in February, resolved in March: March counts the resolution only.
    ticket = resolved(madrid(3, 10, 9), madrid(3, 10, 12))
    ticket.first_response_at = madrid(2, 20, 9)
    ticket.first_response_due_at = madrid(2, 20, 10)
    ticket.save()

    march = row(get(AgentUserFactory(), **MARCH), "2026-03-01", "all")

    assert (march["first_responses"], march["resolutions"]) == (0, 1)


def test_every_month_and_priority_comes_back_even_without_data():
    body = get(AgentUserFactory(), start="2026-01-15", end="2026-03-10")

    assert [(r["month"], r["priority"]) for r in body["results"]] == [
        (month, priority)
        for month in ("2026-01-01", "2026-02-01", "2026-03-01")
        for priority in [*Priority.values, "all"]
    ]
    empty = body["results"][0]
    assert (empty["first_responses"], empty["first_response_rate"]) == (0, None)


def test_staff_can_narrow_to_one_client_and_clients_only_see_theirs():
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    for organization in (mine, theirs):
        answered(madrid(3, 2, 9), madrid(3, 2, 10), organization=organization)
        resolved(madrid(3, 10, 9), madrid(3, 10, 12), organization=organization)
    customer = ClientUserFactory(organization=mine)

    every = row(get(AgentUserFactory(), **MARCH), "2026-03-01", "all")
    one = row(get(AgentUserFactory(), organization=mine.pk, **MARCH), "2026-03-01", "all")
    own = row(get(customer, **MARCH), "2026-03-01", "all")

    # Per client: one first response and one resolution in March (the resolved
    # ticket was answered in January, outside the period).
    assert (every["first_responses"], every["resolutions"]) == (2, 2)
    assert (one["first_responses"], one["resolutions"]) == (1, 1)
    assert (own["first_responses"], own["resolutions"]) == (1, 1)
