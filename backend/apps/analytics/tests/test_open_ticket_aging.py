"""GET /api/analytics/open-ticket-aging/: sql/open_ticket_aging.sql, a snapshot.

The view takes the instant once; the tests fix it by patching timezone.now.
"""

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("analytics:open-ticket-aging")
NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)
BUCKETS = ["under_1_day", "1_to_3_days", "3_to_7_days", "over_7_days"]


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


def ago(**delta):
    return NOW - dt.timedelta(**delta)


def later(**delta):
    return NOW + dt.timedelta(**delta)


def open_ticket(age, status="open", **kwargs):
    """Answered, with both deadlines in the future unless given."""
    fields = {
        "status": status,
        "created_at": NOW - age,
        "first_response_at": NOW - age + dt.timedelta(minutes=5),
        "first_response_business_minutes": 5,
        "first_response_due_at": NOW - age + dt.timedelta(hours=1),
        "resolution_due_at": later(days=30),
    }
    if status == "pending":
        fields["sla_paused_at"] = ago(minutes=1)
    if status == "new":
        fields.update(first_response_at=None, first_response_business_minutes=None)
        fields["first_response_due_at"] = later(hours=1)
    return TicketFactory(**{**fields, **kwargs})


def get(user, **params):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(URL, params)


def by_bucket(response):
    assert response.status_code == 200, response.json()
    return {row["bucket"]: row for row in response.json()["results"]}


def test_open_tickets_by_age_and_status():
    open_ticket(dt.timedelta(hours=2), status="new")
    open_ticket(dt.timedelta(hours=23, minutes=59))
    open_ticket(dt.timedelta(days=1))  # exactly one day: the next bucket
    open_ticket(dt.timedelta(days=2), status="pending")
    open_ticket(dt.timedelta(days=30))

    rows = by_bucket(get(AgentUserFactory()))

    assert list(rows) == BUCKETS
    counts = {
        bucket: (r["tickets"], r["new"], r["open"], r["pending"]) for bucket, r in rows.items()
    }
    assert counts == {
        "under_1_day": (2, 1, 1, 0),
        "1_to_3_days": (2, 0, 1, 1),
        "3_to_7_days": (0, 0, 0, 0),
        "over_7_days": (1, 0, 1, 0),
    }
    assert (rows["over_7_days"]["min_days"], rows["over_7_days"]["max_days"]) == (7, None)


def test_resolved_and_closed_tickets_are_not_open_work():
    TicketFactory(resolved=True, created_at=ago(days=2))
    TicketFactory(closed=True, created_at=ago(days=2))

    rows = by_bucket(get(AgentUserFactory()))

    assert sum(row["tickets"] for row in rows.values()) == 0


def test_resolution_breaches_follow_the_clock():
    open_ticket(dt.timedelta(days=2), resolution_due_at=ago(minutes=1))  # running, late
    open_ticket(dt.timedelta(days=2), resolution_due_at=NOW)  # due now: breached
    # Paused after its deadline: already breached.
    open_ticket(
        dt.timedelta(days=2),
        status="pending",
        resolution_due_at=ago(hours=5),
        sla_paused_at=ago(hours=1),
    )
    # Paused before its deadline: the clock stopped in time.
    open_ticket(
        dt.timedelta(days=2),
        status="pending",
        resolution_due_at=ago(hours=1),
        sla_paused_at=ago(hours=5),
    )

    row = by_bucket(get(AgentUserFactory()))["1_to_3_days"]

    assert (row["tickets"], row["resolution_breached"]) == (4, 3)


def test_first_responses_overdue_only_while_unanswered():
    open_ticket(dt.timedelta(hours=3), status="new", first_response_due_at=ago(hours=1))
    open_ticket(dt.timedelta(hours=3), status="new", first_response_due_at=later(hours=1))
    # Answered late: no longer overdue (sla-compliance counts that breach).
    open_ticket(dt.timedelta(hours=3), first_response_due_at=ago(hours=2, minutes=58))

    row = by_bucket(get(AgentUserFactory()))["under_1_day"]

    assert (row["tickets"], row["first_response_overdue"]) == (3, 1)


def test_response_says_when_and_for_whom():
    body = get(AgentUserFactory()).json()

    assert dt.datetime.fromisoformat(body["as_of"]) == NOW
    assert body["organization"] is None


def test_a_snapshot_takes_no_period():
    response = get(AgentUserFactory(), start="2026-01-01", end="2026-01-31")

    assert response.status_code == 400
    assert set(response.json()) == {"start", "end"}


def test_staff_can_narrow_to_one_client_and_clients_only_see_theirs():
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    open_ticket(dt.timedelta(hours=1), organization=mine)
    open_ticket(dt.timedelta(hours=1), organization=theirs)
    customer = ClientUserFactory(organization=mine)

    every = by_bucket(get(AgentUserFactory()))["under_1_day"]["tickets"]
    one = by_bucket(get(AgentUserFactory(), organization=mine.pk))["under_1_day"]["tickets"]
    own = get(customer)

    assert (every, one) == (2, 1)
    assert by_bucket(own)["under_1_day"]["tickets"] == 1
    assert own.json()["organization"] == mine.pk
    assert get(customer, organization=theirs.pk).status_code == 403
