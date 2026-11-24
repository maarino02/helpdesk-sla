"""GET /api/analytics/response-times/: sql/response_times.sql on a hand-built dataset.

percentile_cont interpolates: for sorted values v0..vn-1 the percentile p sits at
position p * (n - 1). Median of 10, 20, 30, 100: position 1.5, so 25 (the average
would be 40). p90: position 2.7, so 30 + 0.7 * 70 = 79.
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

URL = reverse("analytics:response-times")
MADRID = ZoneInfo("Europe/Madrid")
MARCH = {"start": "2026-03-01", "end": "2026-03-31"}
CREATED = dt.datetime(2026, 3, 2, 9, 0, tzinfo=MADRID)


def answered(business_minutes, wall_minutes, at=None, **kwargs):
    """Answered `wall_minutes` after creation, `business_minutes` of which counted."""
    at = at or CREATED + dt.timedelta(minutes=wall_minutes)
    return TicketFactory(
        status="open",
        created_at=at - dt.timedelta(minutes=wall_minutes),
        first_response_at=at,
        first_response_business_minutes=business_minutes,
        first_response_due_at=at + dt.timedelta(hours=1),
        **kwargs,
    )


def resolved(business_minutes, wall_minutes, **kwargs):
    """Resolved `wall_minutes` after creation, answered in January (outside March)."""
    at = CREATED + dt.timedelta(minutes=wall_minutes)
    return TicketFactory(
        closed=True,
        created_at=at - dt.timedelta(minutes=wall_minutes),
        resolved_at=at,
        resolution_business_minutes=business_minutes,
        resolution_due_at=at + dt.timedelta(hours=1),
        closed_at=at + dt.timedelta(days=1),
        first_response_at=dt.datetime(2026, 1, 5, 10, tzinfo=MADRID),
        first_response_business_minutes=5,
        first_response_due_at=dt.datetime(2026, 1, 5, 12, tzinfo=MADRID),
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


def test_median_p90_and_wall_clock_median_per_priority():
    for business, wall in [(10, 60), (20, 120), (30, 180), (100, 240)]:
        answered(business, wall)
    # Skewed on purpose: the median (200) is not the average (300).
    for business, wall in [(100, 1000), (200, 2000), (600, 6000)]:
        resolved(business, wall)

    medium = row(get(AgentUserFactory(), **MARCH), "2026-03-01", Priority.MEDIUM)

    assert medium == {
        "month": "2026-03-01",
        "priority": "medium",
        "first_responses": 4,
        "first_response_median": 25.0,
        "first_response_p90": 79.0,
        "first_response_median_wall": 150.0,
        "resolutions": 3,
        "resolution_median": 200.0,
        "resolution_p90": 520.0,  # position 1.8: 200 + 0.8 * 400
        "resolution_median_wall": 2000.0,
    }


def test_the_all_row_takes_percentiles_over_every_priority():
    # Not an average of the priorities' medians: the percentile of all values.
    for business in (10, 20, 30, 40):
        answered(business, 60)
    answered(50, 60, priority=Priority.HIGH)

    total = row(get(AgentUserFactory(), **MARCH), "2026-03-01", "all")

    # 10..50: median at position 2 (30); p90 at 3.6 (40 + 0.6 * 10).
    assert (total["first_responses"], total["first_response_median"]) == (5, 30.0)
    assert total["first_response_p90"] == 46.0


def test_months_without_outcomes_have_no_times():
    body = get(AgentUserFactory(), start="2026-01-01", end="2026-02-28")

    assert len(body["results"]) == 2 * (len(Priority.values) + 1)
    assert all(r["first_responses"] == 0 for r in body["results"])
    assert all(r["first_response_median"] is None for r in body["results"])
    assert all(r["resolution_p90"] is None for r in body["results"])


def test_outcomes_count_in_their_month_in_the_provider_time_zone():
    # 31 March 22:30 UTC is 1 April 00:30 in Madrid.
    answered(10, 60, at=dt.datetime(2026, 3, 31, 22, 30, tzinfo=dt.UTC))
    ticket = resolved(100, 100)
    ticket.resolved_at = dt.datetime(2026, 3, 31, 22, 30, tzinfo=dt.UTC)
    ticket.save()

    body = get(AgentUserFactory(), start="2026-03-01", end="2026-04-30")

    march, april = row(body, "2026-03-01", "all"), row(body, "2026-04-01", "all")
    assert (march["first_responses"], march["resolutions"]) == (0, 0)
    assert (april["first_responses"], april["resolutions"]) == (1, 1)


def test_staff_can_narrow_to_one_client_and_clients_only_see_theirs():
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    answered(10, 60, organization=mine)
    answered(90, 60, organization=theirs)
    resolved(100, 100, organization=mine)
    resolved(900, 100, organization=theirs)
    customer = ClientUserFactory(organization=mine)

    one = row(get(AgentUserFactory(), organization=mine.pk, **MARCH), "2026-03-01", "all")
    own = row(get(customer, **MARCH), "2026-03-01", "all")

    for body in (one, own):
        assert (body["first_response_median"], body["resolution_median"]) == (10.0, 100.0)
