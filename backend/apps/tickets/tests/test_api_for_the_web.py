"""What the API adds for the web pages: allowed_transitions and sla_state.

As in test_api.py, force_authenticate keeps the test's system context.
"""

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.tickets.models import Status
from apps.tickets.services.state_machine import allowed_transitions, transition
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

# Written out from docs/state-machine.md, not derived from TRANSITIONS: the test
# must not compare the table with itself.
EXPECTED = {
    "staff": {
        Status.NEW: [Status.OPEN],
        Status.OPEN: [Status.PENDING, Status.RESOLVED],
        Status.PENDING: [Status.OPEN, Status.RESOLVED],
        Status.RESOLVED: [Status.OPEN],
        Status.CLOSED: [],
    },
    "client": {
        Status.NEW: [],
        Status.OPEN: [],
        Status.PENDING: [Status.OPEN],
        Status.RESOLVED: [Status.OPEN, Status.CLOSED],
        Status.CLOSED: [],
    },
}


def api_as(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def make(status, **kwargs):
    fields = {"status": status}
    if status in (Status.RESOLVED, Status.CLOSED):
        fields["resolved_at"] = dt.datetime.now(dt.UTC)
        fields["resolution_business_minutes"] = 60
    if status == Status.CLOSED:
        fields["closed_at"] = dt.datetime.now(dt.UTC)
    return TicketFactory(**fields, **kwargs)


@pytest.mark.parametrize("status", list(Status))
@pytest.mark.parametrize("make_user", [AgentUserFactory, AdminUserFactory, ClientUserFactory])
def test_allowed_transitions_by_status_and_role(status, make_user):
    user = make_user()
    kind = "client" if user.organization_id else "staff"
    # A client's own organization; for staff, any.
    ticket = make(status, **({"organization": user.organization} if kind == "client" else {}))

    assert allowed_transitions(ticket, user) == EXPECTED[kind][status]


def test_a_client_gets_nothing_on_another_organizations_ticket():
    ticket = make(Status.RESOLVED)

    assert allowed_transitions(ticket, ClientUserFactory()) == []


@pytest.mark.parametrize("status", list(Status))
def test_every_offered_transition_is_accepted(status):
    # The point of allowed_transitions: what the web offers, transition() accepts.
    agent, customer = AgentUserFactory(), ClientUserFactory()
    for user in (agent, customer):
        for to_status in allowed_transitions(
            make(status, organization=customer.organization), user
        ):
            ticket = make(status, organization=customer.organization)
            assert transition(ticket, to_status, user).status == to_status


def test_the_detail_says_what_the_user_may_do():
    customer = ClientUserFactory()
    ticket = make(Status.RESOLVED, organization=customer.organization)

    body = api_as(customer).get(reverse("tickets:detail", args=[ticket.pk])).json()

    assert body["allowed_transitions"] == [Status.OPEN, Status.CLOSED]
    assert body["sla_state"] == "met"


def test_the_list_has_the_sla_state_but_not_the_transitions():
    ticket = TicketFactory()

    [row] = api_as(AgentUserFactory()).get(reverse("tickets:list")).json()["results"]

    assert row["id"] == ticket.pk
    assert row["sla_state"] == "on_track"
    assert "allowed_transitions" not in row


def test_a_transition_answers_with_the_next_transitions():
    agent = AgentUserFactory()
    ticket = TicketFactory()

    response = api_as(agent).post(
        reverse("tickets:transitions", args=[ticket.pk]), {"to_status": "open"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["allowed_transitions"] == [Status.PENDING, Status.RESOLVED]


def test_one_instant_for_the_whole_list(monkeypatch):
    TicketFactory.create_batch(3)
    calls = []
    real_now = dt.datetime.now(dt.UTC)

    def now():
        calls.append(1)
        return real_now

    monkeypatch.setattr("apps.tickets.serializers.timezone.now", now)
    api_as(AgentUserFactory()).get(reverse("tickets:list"))

    assert len(calls) == 1
