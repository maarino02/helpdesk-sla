"""GET /api/analytics/agent-workload/: sql/agent_workload.sql, staff only.

The open work is judged at the view's instant (timezone.now, patched here); the
resolutions are those of the period.
"""

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("analytics:agent-workload")
NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)
NOVEMBER = {"start": "2026-11-01", "end": "2026-11-30"}


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)


def ago(**delta):
    return NOW - dt.timedelta(**delta)


def later(**delta):
    return NOW + dt.timedelta(**delta)


def open_ticket(assignee, status="open", **kwargs):
    """Answered two days ago, resolution due in the future unless given."""
    fields = {
        "status": status,
        "assignee": assignee,
        "created_at": ago(days=2),
        "first_response_at": ago(days=2) + dt.timedelta(minutes=5),
        "first_response_business_minutes": 5,
        "first_response_due_at": ago(days=2) + dt.timedelta(hours=1),
        "resolution_due_at": later(days=30),
    }
    if status == "pending":
        fields["sla_paused_at"] = ago(minutes=1)
    if status == "new":
        fields.update(first_response_at=None, first_response_business_minutes=None)
        fields["first_response_due_at"] = later(hours=1)
    return TicketFactory(**{**fields, **kwargs})


def resolved_ticket(assignee, at, minutes, due=None, **kwargs):
    """Resolved at `at` after `minutes` business minutes; on time unless `due` says not."""
    return TicketFactory(
        resolved=True,
        assignee=assignee,
        created_at=at - dt.timedelta(days=3),
        resolved_at=at,
        sla_paused_at=at,
        resolution_business_minutes=minutes,
        resolution_due_at=due or at + dt.timedelta(hours=1),
        **kwargs,
    )


def get(user, **params):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(URL, {**NOVEMBER, **params})


def results(response):
    assert response.status_code == 200, response.json()
    return response.json()["results"]


def by_agent(response):
    return {row["agent_id"]: row for row in results(response)}


def figures(row):
    return (
        row["open_tickets"],
        row["pending"],
        row["open_breached"],
        row["resolved"],
        row["resolution_median"],
        row["resolved_late"],
    )


def test_open_work_and_output_per_agent():
    busy, idle = AgentUserFactory(), AgentUserFactory()
    open_ticket(busy)
    open_ticket(busy, status="pending")
    open_ticket(busy, resolution_due_at=ago(minutes=1))  # late
    # Two open and three resolved: joining before counting would give six of each.
    resolved_ticket(busy, ago(days=1), 10)
    resolved_ticket(busy, ago(days=2), 20, due=ago(days=2))  # due at the instant: late
    resolved_ticket(busy, ago(days=3), 90)
    resolved_ticket(busy, dt.datetime(2026, 10, 15, tzinfo=dt.UTC), 999)  # before the period

    rows = by_agent(get(idle))

    assert figures(rows[busy.pk]) == (3, 1, 1, 3, 20.0, 1)
    assert figures(rows[idle.pk]) == (0, 0, 0, 0, None, 0)


def test_open_breaches_follow_the_clock():
    agent = AgentUserFactory()
    open_ticket(agent, resolution_due_at=NOW)  # due now: breached
    # Paused after its deadline: already breached.
    open_ticket(agent, status="pending", resolution_due_at=ago(hours=5), sla_paused_at=ago(hours=1))
    # Paused before its deadline: the clock stopped in time.
    open_ticket(agent, status="pending", resolution_due_at=ago(hours=1), sla_paused_at=ago(hours=5))

    row = by_agent(get(agent))[agent.pk]

    assert (row["open_tickets"], row["pending"], row["open_breached"]) == (3, 2, 2)


def test_closed_tickets_are_not_open_work_but_count_as_resolved():
    agent = AgentUserFactory()
    TicketFactory(closed=True, assignee=agent, resolved_at=ago(days=1), created_at=ago(days=4))

    row = by_agent(get(agent))[agent.pk]

    assert (row["open_tickets"], row["resolved"]) == (0, 1)


def test_unassigned_tickets_have_their_own_row_last():
    agent = AgentUserFactory()
    open_ticket(None, status="new")
    resolved_ticket(None, ago(days=1), 30)

    rows = results(get(agent))

    assert [row["agent_id"] for row in rows] == [agent.pk, None]
    assert figures(rows[-1]) == (1, 0, 0, 1, 30.0, 0)
    assert (rows[-1]["full_name"], rows[-1]["role"]) == (None, None)


def test_who_gets_a_row():
    agent, admin = AgentUserFactory(), AdminUserFactory()
    gone_with_work = AgentUserFactory(is_active=False)
    open_ticket(gone_with_work)
    gone_with_output = AgentUserFactory(is_active=False)
    resolved_ticket(gone_with_output, ago(days=1), 10)
    gone_idle = AgentUserFactory(is_active=False)
    customer = ClientUserFactory()

    rows = by_agent(get(agent))

    assert set(rows) == {agent.pk, admin.pk, gone_with_work.pk, gone_with_output.pk, None}
    assert gone_idle.pk not in rows and customer.pk not in rows
    assert rows[gone_with_work.pk]["is_active"] is False
    assert rows[admin.pk]["role"] == "admin"


def test_busiest_first_then_by_name():
    ana = AgentUserFactory(full_name="Ana")
    AgentUserFactory(full_name="Bea")
    carlos = AgentUserFactory(full_name="Carlos")
    open_ticket(carlos)

    rows = results(get(ana))

    assert [row["full_name"] for row in rows] == ["Carlos", "Ana", "Bea", None]


def test_narrowing_to_a_client_keeps_everyone():
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    works_for_mine, works_for_theirs = AgentUserFactory(), AgentUserFactory()
    open_ticket(works_for_mine, organization=mine)
    resolved_ticket(works_for_mine, ago(days=1), 10, organization=mine)
    open_ticket(works_for_theirs, organization=theirs)
    resolved_ticket(works_for_theirs, ago(days=1), 10, organization=theirs)

    rows = by_agent(get(works_for_mine, organization=mine.pk))

    assert (rows[works_for_mine.pk]["open_tickets"], rows[works_for_mine.pk]["resolved"]) == (1, 1)
    # Nothing for this client, but still listed, with zeros.
    assert figures(rows[works_for_theirs.pk]) == (0, 0, 0, 0, None, 0)


def test_response_says_when_and_for_which_period():
    body = get(AgentUserFactory()).json()

    assert dt.datetime.fromisoformat(body["as_of"]) == NOW
    assert body["period"]["start"] == "2026-11-01"
    assert body["period"]["end"] == "2026-11-30"


def test_clients_are_forbidden():
    customer = ClientUserFactory()

    response = get(customer, organization=customer.organization_id)

    assert response.status_code == 403
