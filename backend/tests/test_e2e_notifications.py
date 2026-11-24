"""Notifications end to end: real request transactions, commits and on_commit.

The tasks run in the same process instead of going through the broker (tests
never reach a real one), so the whole chain request -> commit -> notify ->
send_notification -> outbox is exercised.
"""

import pytest
from django.core import mail
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory
from apps.core.rls import system_context
from apps.tickets import tasks
from apps.tickets.models import Status, Ticket
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.usefixtures("production_like")

PASSWORD = "password"  # The factory default.


def logged_in(user):
    api = APIClient()
    tokens = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api


@pytest.fixture
def open_ticket():
    with system_context():
        agent = AgentUserFactory()
        ticket = TicketFactory(status=Status.OPEN, assignee=agent)
    return ticket, agent


def resolve(api, ticket):
    return api.post(f"/api/tickets/{ticket.pk}/transitions/", {"to_status": Status.RESOLVED})


def test_resolving_a_ticket_emails_the_requester(open_ticket, monkeypatch):
    monkeypatch.setattr(tasks.notify, "delay", tasks.notify)
    monkeypatch.setattr(tasks.send_notification, "delay", tasks.send_notification)
    ticket, agent = open_ticket

    response = resolve(logged_in(agent), ticket)

    assert response.status_code == 200
    [message] = mail.outbox
    assert message.to == [ticket.requester.email]
    assert "Incidencia resuelta" in message.subject


def test_broker_outage_does_not_fail_the_committed_request(open_ticket, monkeypatch):
    def broker_down(*args):
        raise OperationalError("Error 111 connecting to redis:6379. Connection refused.")

    monkeypatch.setattr(tasks.notify, "delay", broker_down)
    ticket, agent = open_ticket

    response = resolve(logged_in(agent), ticket)

    # robust=True: the error is logged, the client sees the committed change.
    assert response.status_code == 200
    with system_context():
        assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED
    assert mail.outbox == []
