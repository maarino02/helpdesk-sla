"""transition(): the transition table, its SLA effects and its events.

Concurrency (two connections racing for the same ticket): backend/tests/.
"""

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.state_machine import (
    TRANSITIONS,
    TransitionNotAllowed,
    TransitionNotDefined,
    transition,
)
from apps.tickets.tests.factories import CategoryFactory, TicketFactory
from tests.rls import set_context

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def minutes(n):
    return NOW + timedelta(minutes=n)


# Every status pair for every kind of actor: 25 x 4 = 100 cases.

ROLES = ["client", "agent", "admin", "system"]


def ticket_in(status):
    traits = {
        Status.RESOLVED: {"resolved": True},
        Status.CLOSED: {"closed": True},
    }
    return TicketFactory(**traits.get(status, {"status": status}))


def actor_for(role, ticket):
    return {
        "client": lambda: ClientUserFactory(organization=ticket.organization),
        "agent": AgentUserFactory,
        "admin": AdminUserFactory,
        "system": lambda: None,
    }[role]()


@pytest.mark.parametrize(
    ("from_status", "to_status", "role"), list(product(Status.values, Status.values, ROLES))
)
def test_transition_table(from_status, to_status, role):
    ticket = ticket_in(from_status)
    actor = actor_for(role, ticket)
    allowed = TRANSITIONS.get((from_status, to_status))

    if allowed is None:
        with pytest.raises(TransitionNotDefined):
            transition(ticket, to_status, actor)
    elif role not in allowed:
        with pytest.raises(TransitionNotAllowed):
            transition(ticket, to_status, actor)
    else:
        updated = transition(ticket, to_status, actor)
        assert updated.status == to_status
        event = TicketEvent.objects.get(ticket=ticket, event_type=EventType.STATUS_CHANGED)
        assert (event.from_value, event.to_value, event.actor) == (from_status, to_status, actor)
        return
    # Rejected: nothing changed and nothing was recorded.
    assert Ticket.objects.get(pk=ticket.pk).status == from_status
    assert not TicketEvent.objects.filter(ticket=ticket).exists()


def test_table_matches_the_documented_count():
    # docs/state-machine.md has 7 transitions; this fails if one is added or
    # removed here without updating the document and this test.
    assert len(TRANSITIONS) == 7


def test_client_of_another_organization_cannot_act():
    ticket = ticket_in(Status.RESOLVED)

    with pytest.raises(TransitionNotAllowed):
        transition(ticket, Status.OPEN, ClientUserFactory())


def test_unknown_status_is_a_programming_error():
    with pytest.raises(ValueError, match="Unknown status"):
        transition(TicketFactory(), "archived", AgentUserFactory())


def test_status_is_read_from_the_locked_row_not_from_the_object():
    ticket = ticket_in(Status.RESOLVED)
    stale = Ticket.objects.get(pk=ticket.pk)
    transition(ticket, Status.CLOSED, None)

    # The stale object still says resolved; the database says closed.
    with pytest.raises(TransitionNotDefined):
        transition(stale, Status.OPEN, ClientUserFactory(organization=ticket.organization))


# Effects. Tickets are created at NOW with a 480-minute target (deadline NOW+480),
# and business time is provisionally wall-clock time, so values are exact.


@pytest.fixture
def agent():
    return AgentUserFactory()


@pytest.fixture
def ticket(agent):
    SlaPolicyFactory(priority=Priority.MEDIUM, first_response_minutes=60, resolution_minutes=480)
    created = create_ticket(
        requester=ClientUserFactory(),
        title="Sin red",
        description="No hay conexión.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=NOW,
    )
    return transition(created, Status.OPEN, agent, now=minutes(10))


class TestEffects:
    def test_taking_an_unassigned_ticket_assigns_it(self, ticket, agent):
        assert ticket.assignee == agent
        event = TicketEvent.objects.get(ticket=ticket, event_type=EventType.ASSIGNEE_CHANGED)
        assert (event.from_value, event.to_value, event.actor) == (None, str(agent.pk), agent)

    def test_taking_an_assigned_ticket_keeps_the_assignee(self):
        owner = AgentUserFactory()
        ticket = TicketFactory(assignee=owner)

        updated = transition(ticket, Status.OPEN, AdminUserFactory())

        assert updated.assignee == owner
        assert not TicketEvent.objects.filter(event_type=EventType.ASSIGNEE_CHANGED).exists()

    def test_pending_pauses_the_clock(self, ticket, agent):
        updated = transition(ticket, Status.PENDING, agent, now=minutes(60))

        assert updated.sla_paused_at == minutes(60)
        assert updated.resolution_due_at == minutes(480)  # Unchanged until resumed.

    def test_resuming_extends_the_deadline_by_the_pause(self, ticket, agent):
        transition(ticket, Status.PENDING, agent, now=minutes(60))
        client = ClientUserFactory(organization=ticket.organization)

        updated = transition(ticket, Status.OPEN, client, now=minutes(90))

        assert updated.resolution_due_at == minutes(480 + 30)
        assert updated.sla_paused_at is None

    def test_resolution_minutes_exclude_the_pauses(self, ticket, agent):
        transition(ticket, Status.PENDING, agent, now=minutes(60))
        transition(ticket, Status.OPEN, agent, now=minutes(90))

        updated = transition(ticket, Status.RESOLVED, agent, now=minutes(120))

        # 120 minutes since creation, 30 of them paused.
        assert updated.resolution_business_minutes == 90
        assert updated.resolved_at == minutes(120)
        assert updated.sla_paused_at == minutes(120)  # Paused while resolved.

    def test_resolving_from_pending_counts_the_pause_first(self, ticket, agent):
        transition(ticket, Status.PENDING, agent, now=minutes(60))

        updated = transition(ticket, Status.RESOLVED, agent, now=minutes(100))

        assert updated.resolution_due_at == minutes(480 + 40)
        assert updated.resolution_business_minutes == 60  # 100 - 40 paused.
        assert updated.sla_paused_at == minutes(100)

    def test_reopening_clears_the_resolution_and_extends_the_deadline(self, ticket, agent):
        transition(ticket, Status.RESOLVED, agent, now=minutes(120))
        client = ClientUserFactory(organization=ticket.organization)

        updated = transition(ticket, Status.OPEN, client, now=minutes(180))

        assert (updated.resolved_at, updated.resolution_business_minutes) == (None, None)
        assert updated.resolution_due_at == minutes(480 + 60)
        assert updated.sla_paused_at is None

    def test_resolving_again_after_a_reopen(self, ticket, agent):
        transition(ticket, Status.RESOLVED, agent, now=minutes(120))
        transition(ticket, Status.OPEN, agent, now=minutes(180))

        updated = transition(ticket, Status.RESOLVED, agent, now=minutes(200))

        # 200 minutes since creation, 60 of them resolved (paused).
        assert updated.resolution_business_minutes == 140

    def test_resolving_late_counts_more_than_the_target(self, ticket, agent):
        updated = transition(ticket, Status.RESOLVED, agent, now=minutes(500))

        # left = -20: the deadline passed 20 minutes ago.
        assert updated.resolution_business_minutes == 500
        assert updated.resolved_at > updated.resolution_due_at

    def test_closing_stops_the_clock_and_keeps_the_resolution(self, ticket, agent):
        transition(ticket, Status.RESOLVED, agent, now=minutes(120))

        updated = transition(ticket, Status.CLOSED, None, now=minutes(300))

        assert updated.closed_at == minutes(300)
        assert updated.sla_paused_at is None
        assert updated.resolution_due_at == minutes(480)  # Not extended on closing.
        assert (updated.resolved_at, updated.resolution_business_minutes) == (minutes(120), 120)

    def test_system_events_have_no_actor(self, ticket, agent):
        transition(ticket, Status.RESOLVED, agent, now=minutes(120))
        transition(ticket, Status.CLOSED, None, now=minutes(300))

        event = TicketEvent.objects.get(ticket=ticket, to_value=Status.CLOSED)
        assert event.actor is None

    def test_works_in_the_client_row_level_security_context(self, ticket, agent):
        transition(ticket, Status.RESOLVED, agent, now=minutes(120))
        client = ClientUserFactory(organization=ticket.organization)
        set_context(role="client", org_id=ticket.organization_id, user_id=client.pk)

        updated = transition(ticket, Status.OPEN, client, now=minutes(180))

        assert updated.status == Status.OPEN
