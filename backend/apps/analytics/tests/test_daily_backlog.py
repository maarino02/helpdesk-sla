"""GET /api/analytics/daily-backlog/: sql/daily_backlog.sql.

The query reads only ticket_event, so each test writes the history it needs; the
tickets' own status does not take part.
"""

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.models import EventType
from apps.tickets.tests.factories import TicketEventFactory, TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("analytics:daily-backlog")
NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)
# Ends in the future: the days stop at today.
WEEK = {"start": "2026-11-20", "end": "2026-11-30"}


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


def at(day, hour=10):
    return dt.datetime(2026, 11, day, hour, tzinfo=dt.UTC)


def history(created, *changes, **kwargs):
    """A ticket created at `created` and moved by (instant, from, to) changes."""
    ticket = TicketFactory(**kwargs)
    TicketEventFactory(ticket=ticket, event_type=EventType.CREATED, created_at=created)
    for instant, before, after in changes:
        TicketEventFactory(
            ticket=ticket,
            event_type=EventType.STATUS_CHANGED,
            from_value=before,
            to_value=after,
            created_at=instant,
        )
    return ticket


def get(user, **params):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(URL, {**WEEK, **params})


def results(response):
    assert response.status_code == 200, response.json()
    return response.json()["results"]


def by_day(response):
    return {row["day"]: row for row in results(response)}


def test_backlog_per_status_at_the_end_of_each_day():
    # Before the period: open on the 19th, resolved on the 23rd.
    history(
        at(18),
        (at(19), "new", "open"),
        (at(21), "open", "pending"),
        (at(22), "pending", "open"),
        (at(23), "open", "resolved"),
    )
    # Resolved before the period, reopened during it.
    history(at(10), (at(10, 12), "new", "open"), (at(12), "open", "resolved"))
    history(
        at(9),
        (at(9, 12), "new", "open"),
        (at(11), "open", "resolved"),
        (at(21), "resolved", "open"),
    )
    history(at(21), (at(21, 11), "new", "open"))  # created and taken the same day
    history(at(22))  # still new

    rows = results(get(AgentUserFactory()))

    figures = {
        row["day"]: (
            row["backlog"],
            row["new"],
            row["open"],
            row["pending"],
            row["created"],
            row["resolved"],
        )
        for row in rows
    }
    assert figures == {
        "2026-11-20": (1, 0, 1, 0, 0, 0),
        "2026-11-21": (3, 0, 2, 1, 1, 0),
        "2026-11-22": (4, 1, 3, 0, 1, 0),
        "2026-11-23": (3, 1, 2, 0, 0, 1),
        "2026-11-24": (3, 1, 2, 0, 0, 0),
    }
    assert [row["day"] for row in rows] == sorted(figures)  # oldest first, to today


def test_closing_a_resolved_ticket_changes_nothing():
    history(
        at(18),
        (at(18, 11), "new", "open"),
        (at(21), "open", "resolved"),
        (at(22), "resolved", "closed"),
    )

    rows = by_day(get(AgentUserFactory()))

    assert [rows[day]["backlog"] for day in sorted(rows)] == [1, 0, 0, 0, 0]


def test_days_are_those_of_the_provider_time_zone():
    # 23:30 UTC on the 20th is 00:30 on the 21st in Madrid.
    history(dt.datetime(2026, 11, 20, 23, 30, tzinfo=dt.UTC))

    rows = by_day(get(AgentUserFactory()))

    assert (rows["2026-11-20"]["backlog"], rows["2026-11-21"]["backlog"]) == (0, 1)
    assert rows["2026-11-21"]["created"] == 1


def test_only_status_events_count():
    ticket = history(at(18))
    for event_type, before, after in [
        (EventType.PRIORITY_CHANGED, "low", "high"),
        (EventType.ASSIGNEE_CHANGED, None, "7"),
        (EventType.CATEGORY_CHANGED, "1", "2"),
    ]:
        TicketEventFactory(
            ticket=ticket,
            event_type=event_type,
            from_value=before,
            to_value=after,
            created_at=at(21),
        )

    rows = by_day(get(AgentUserFactory()))

    assert {row["backlog"] for row in rows.values()} == {1}


def test_a_period_in_the_past_ends_on_its_last_day():
    history(at(18))

    rows = results(get(AgentUserFactory(), start="2026-11-01", end="2026-11-19"))

    assert len(rows) == 19
    assert (rows[16]["backlog"], rows[17]["backlog"], rows[-1]["day"]) == (0, 1, "2026-11-19")


def test_response_says_when_and_for_which_period():
    body = get(AgentUserFactory()).json()

    assert dt.datetime.fromisoformat(body["as_of"]) == NOW
    assert body["period"]["end"] == "2026-11-30"


def test_staff_can_narrow_to_one_client_and_clients_only_see_theirs():
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    history(at(18), organization=mine)
    history(at(18), organization=theirs)
    history(at(18), organization=theirs)
    customer = ClientUserFactory(organization=mine)

    def today(response):
        return by_day(response)["2026-11-24"]["backlog"]

    assert today(get(AgentUserFactory())) == 3
    assert today(get(AgentUserFactory(), organization=mine.pk)) == 1
    assert today(get(customer)) == 1
    assert get(customer, organization=theirs.pk).status_code == 403
