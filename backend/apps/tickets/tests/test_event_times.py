"""Events are stamped with the instant the service was given, not the database clock.

The same instant is stored on the ticket (created_at, resolved_at...), so the
history and the ticket agree, also when the demo seed replays past months.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.state_machine import transition
from apps.tickets.services.triage import update_ticket
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.django_db

# Months before the test runs: an event stamped with the database clock
# (the transaction start) cannot match it by chance.
PAST = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def minutes(n):
    return PAST + timedelta(minutes=n)


def test_every_event_takes_the_instant_of_its_service():
    for priority in (Priority.MEDIUM, Priority.HIGH):
        SlaPolicyFactory(priority=priority)
    agent = AgentUserFactory()

    ticket = create_ticket(
        requester=ClientUserFactory(),
        title="Sin acceso al ERP",
        description="Error de credenciales.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=PAST,
    )
    ticket = transition(ticket, Status.OPEN, agent, now=minutes(10))
    ticket = update_ticket(
        ticket,
        agent,
        assignee=AgentUserFactory(),
        category=CategoryFactory(),
        priority=Priority.HIGH,
        now=minutes(20),
    )
    ticket = transition(ticket, Status.RESOLVED, agent, now=minutes(30))

    assert [(event.event_type, event.created_at) for event in ticket.events.all()] == [
        (EventType.CREATED, PAST),
        # new -> open by an agent also assigns the ticket to them.
        (EventType.ASSIGNEE_CHANGED, minutes(10)),
        (EventType.STATUS_CHANGED, minutes(10)),
        (EventType.ASSIGNEE_CHANGED, minutes(20)),
        (EventType.CATEGORY_CHANGED, minutes(20)),
        (EventType.PRIORITY_CHANGED, minutes(20)),
        (EventType.STATUS_CHANGED, minutes(30)),
    ]
    assert ticket.created_at == PAST
    assert ticket.resolved_at == minutes(30)
