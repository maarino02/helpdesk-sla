"""The query runner, the period filters and the base metric view.

Through a probe query (tests/probe.py): the real metrics have their own tests.
force_authenticate keeps the test's system context, as in the tickets API tests;
backend/tests/test_e2e_analytics.py checks row-level security with real logins.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.analytics import queries
from apps.analytics.period import MAX_DAYS
from apps.analytics.tests import probe
from apps.tickets.tests.factories import TicketFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.analytics.tests.probe")]

MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture(autouse=True)
def _probe_queries(tmp_path, monkeypatch):
    probe.install(tmp_path, monkeypatch)
    yield
    queries.load.cache_clear()


def get(user, url="/probe/", **params):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(url, params)


# Runner


def test_rows_come_back_as_dicts_by_column_name():
    params = {
        "start": dt.datetime(2026, 1, 1, tzinfo=MADRID),
        "end": dt.datetime(2027, 1, 1, tzinfo=MADRID),
    }

    rows = queries.run("unfiltered", params)

    assert rows == [{"tickets": 0}]


@pytest.mark.parametrize("name", ["../secrets", "probe.sql", "Probe", ""])
def test_only_bare_query_names_are_loaded(name):
    with pytest.raises(ValueError, match="Invalid query name"):
        queries.load(name)


# Period


def test_dates_become_midnight_bounds_in_the_provider_time_zone():
    response = get(AgentUserFactory(), start="2026-03-01", end="2026-03-31")

    [row] = response.json()["results"]
    # Madrid is UTC+1 in winter and UTC+2 from the last Sunday of March: the end
    # bound is the midnight after the last day, already in summer time.
    assert dt.datetime.fromisoformat(row["period_start"]) == dt.datetime(
        2026, 2, 28, 23, tzinfo=dt.UTC
    )
    assert dt.datetime.fromisoformat(row["period_end"]) == dt.datetime(
        2026, 3, 31, 22, tzinfo=dt.UTC
    )


def test_last_day_is_included_up_to_midnight():
    TicketFactory(created_at=dt.datetime(2026, 3, 31, 23, 59, tzinfo=MADRID))
    TicketFactory(created_at=dt.datetime(2026, 4, 1, 0, 0, tzinfo=MADRID))

    response = get(AgentUserFactory(), start="2026-03-01", end="2026-03-31")

    assert response.json()["results"][0]["tickets"] == 1


def test_default_period_is_the_last_twelve_months(monkeypatch):
    now = dt.datetime(2026, 11, 24, 10, tzinfo=dt.UTC)
    monkeypatch.setattr("django.utils.timezone.now", lambda: now)

    period = get(AgentUserFactory()).json()["period"]

    assert period == {
        "start": "2025-12-01",
        "end": "2026-11-24",
        "organization": None,
        "time_zone": "Europe/Madrid",
    }


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-04-01", "end": "2026-03-01"},  # backwards
        {"start": "2023-01-01", "end": "2026-12-31"},  # too long
        {"start": "2026-02-30"},  # not a date
        {"organization": "0"},
    ],
)
def test_invalid_periods_are_rejected(params):
    response = get(AgentUserFactory(), **params)

    assert response.status_code == 400


def test_the_longest_period_is_accepted_and_one_day_more_is_not():
    # Both days included: MAX_DAYS days from start to end.
    start = dt.date(2024, 1, 1)
    end = start + dt.timedelta(days=MAX_DAYS - 1)
    agent = AgentUserFactory()

    assert get(agent, start=start, end=end).status_code == 200
    assert get(agent, start=start, end=end + dt.timedelta(days=1)).status_code == 400


# Organization


def test_staff_see_every_client_or_the_one_they_ask_for():
    ticket = TicketFactory()
    TicketFactory()
    agent = AgentUserFactory()
    day = ticket.created_at.astimezone(MADRID).date().isoformat()
    params = {"start": day, "end": day}

    every = get(agent, **params).json()
    one = get(agent, organization=ticket.organization_id, **params).json()

    assert every["period"]["organization"] is None
    assert every["results"][0]["tickets"] == 2
    assert one["results"][0]["tickets"] == 1


def test_staff_asking_for_a_missing_client_get_400():
    response = get(AgentUserFactory(), organization=999999)

    assert response.status_code == 400
    assert "organization" in response.json()


def test_a_client_always_gets_their_own_organization():
    customer = ClientUserFactory()

    period = get(customer).json()["period"]

    assert period["organization"] == customer.organization_id


def test_a_client_asking_for_another_organization_is_refused():
    customer = ClientUserFactory()
    other = ClientUserFactory().organization

    response = get(customer, organization=other.pk)

    assert response.status_code == 403
    assert response.json()["code"] == "other_organization"
    # Asking for their own explicitly is fine.
    assert get(customer, organization=customer.organization_id).status_code == 200


def test_anonymous_requests_are_rejected():
    assert APIClient().get("/probe/").status_code == 401
