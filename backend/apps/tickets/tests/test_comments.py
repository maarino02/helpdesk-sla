"""add_comment(): the comment and its effects on the ticket.

Tickets are created at NOW with a medium policy (first response 60, resolution
480). Business time is provisionally wall-clock time, so values are exact.
Concurrency (two agents replying at once): backend/tests/.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status, Ticket, TicketComment, TicketEvent
from apps.tickets.services.comments import (
    CommentNotAllowed,
    CommentsClosed,
    InternalNotesStaffOnly,
    add_comment,
)
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.state_machine import transition
from apps.tickets.tests.factories import CategoryFactory, TicketFactory
from tests.rls import set_context

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def minutes(n):
    return NOW + timedelta(minutes=n)


def fresh(ticket):
    return Ticket.objects.get(pk=ticket.pk)


def status_changes(ticket):
    return list(
        TicketEvent.objects.filter(ticket=ticket, event_type=EventType.STATUS_CHANGED)
        .values_list("from_value", "to_value", "actor")
        .order_by("id")
    )


@pytest.fixture
def agent():
    return AgentUserFactory()


@pytest.fixture
def customer():
    return ClientUserFactory()


@pytest.fixture
def ticket(customer):
    SlaPolicyFactory(priority=Priority.MEDIUM, first_response_minutes=60, resolution_minutes=480)
    return create_ticket(
        requester=customer,
        title="Sin impresora",
        description="No imprime.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=NOW,
    )


class TestFirstResponse:
    def test_first_public_staff_reply(self, ticket, agent):
        comment = add_comment(ticket, agent, "Lo miro ahora.", now=minutes(25))

        updated = fresh(ticket)
        assert comment.created_at == minutes(25)
        assert updated.first_response_at == minutes(25)  # The same instant.
        assert updated.first_response_business_minutes == 25
        # It also takes the ticket: new -> open, assigned to the author.
        assert updated.status == Status.OPEN
        assert updated.assignee == agent
        assert status_changes(ticket) == [("new", "open", agent.pk)]

    def test_later_replies_keep_the_first_response(self, ticket, agent):
        add_comment(ticket, agent, "Lo miro ahora.", now=minutes(25))

        add_comment(ticket, AgentUserFactory(), "¿Algún avance?", now=minutes(90))

        updated = fresh(ticket)
        assert updated.first_response_at == minutes(25)
        assert updated.assignee == agent  # Already assigned.
        assert len(status_changes(ticket)) == 1

    def test_internal_notes_are_not_a_response(self, ticket, agent):
        add_comment(ticket, agent, "Parece el driver.", is_internal=True, now=minutes(25))

        updated = fresh(ticket)
        assert updated.first_response_at is None
        assert updated.status == Status.NEW
        assert updated.assignee is None

    def test_admins_count_as_staff(self, ticket):
        add_comment(ticket, AdminUserFactory(), "Revisado.", now=minutes(5))

        assert fresh(ticket).first_response_at == minutes(5)

    def test_first_reply_on_an_open_ticket_does_not_transition(self, ticket, agent):
        # Taken without replying first: the reply only records the first response.
        transition(ticket, Status.OPEN, agent, now=minutes(10))

        add_comment(ticket, agent, "Ya estoy.", now=minutes(30))

        updated = fresh(ticket)
        assert updated.first_response_at == minutes(30)
        assert len(status_changes(ticket)) == 1

    def test_client_comments_are_not_a_response(self, ticket, customer):
        add_comment(ticket, customer, "Es urgente.", now=minutes(5))

        assert fresh(ticket).first_response_at is None


class TestClientReplies:
    def test_reply_resumes_a_pending_ticket(self, ticket, agent, customer):
        add_comment(ticket, agent, "¿Qué modelo es?", now=minutes(20))
        transition(ticket, Status.PENDING, agent, now=minutes(20))

        add_comment(ticket, customer, "Una HP.", now=minutes(80))

        updated = fresh(ticket)
        assert updated.status == Status.OPEN
        assert updated.resolution_due_at == minutes(480 + 60)  # Paused 60 minutes.
        assert status_changes(ticket)[-1] == ("pending", "open", customer.pk)

    def test_colleague_reply_also_resumes(self, ticket, agent, customer):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.PENDING, agent, now=minutes(20))
        colleague = ClientUserFactory(organization=customer.organization)

        add_comment(ticket, colleague, "Te respondo yo.", now=minutes(30))

        assert fresh(ticket).status == Status.OPEN

    def test_reply_does_not_reopen_a_resolved_ticket(self, ticket, agent, customer):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.RESOLVED, agent, now=minutes(50))

        add_comment(ticket, customer, "¡Gracias, ya funciona!", now=minutes(60))

        assert fresh(ticket).status == Status.RESOLVED

    def test_staff_reply_does_not_resume_a_pending_ticket(self, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.PENDING, agent, now=minutes(20))

        add_comment(ticket, agent, "Recuerda enviarme el modelo.", now=minutes(30))

        assert fresh(ticket).status == Status.PENDING


class TestRejections:
    def test_closed_ticket(self, agent):
        ticket = TicketFactory(closed=True)

        with pytest.raises(CommentsClosed):
            add_comment(ticket, agent, "Hola")

        assert not TicketComment.objects.exists()

    def test_clients_cannot_write_internal_notes(self, ticket, customer):
        with pytest.raises(InternalNotesStaffOnly):
            add_comment(ticket, customer, "Secreto", is_internal=True)

    def test_client_of_another_organization(self, ticket):
        with pytest.raises(CommentNotAllowed):
            add_comment(ticket, ClientUserFactory(), "Hola")

    @pytest.mark.parametrize("body", ["", "   ", "\n\t"])
    def test_empty_body(self, ticket, agent, body):
        with pytest.raises(ValidationError) as exc:
            add_comment(ticket, agent, body)

        assert "body" in exc.value.message_dict
        assert not TicketComment.objects.exists()

    def test_nothing_is_kept_when_an_effect_fails(self, ticket, agent, monkeypatch):
        def failing_transition(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("apps.tickets.services.comments.transition", failing_transition)

        with pytest.raises(RuntimeError):
            add_comment(ticket, agent, "Lo miro.", now=minutes(25))

        # Neither the comment nor the first response survive.
        assert not TicketComment.objects.exists()
        assert fresh(ticket).first_response_at is None


class TestRowLevelSecurity:
    def test_client_context(self, ticket, agent, customer):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.PENDING, agent, now=minutes(20))
        set_context(role="client", org_id=customer.organization_id, user_id=customer.pk)

        add_comment(ticket, customer, "Aquí tienes.", now=minutes(30))

        assert fresh(ticket).status == Status.OPEN

    def test_agent_context(self, ticket, agent):
        set_context(role="agent", user_id=agent.pk)

        add_comment(ticket, agent, "Lo miro.", now=minutes(25))

        assert fresh(ticket).first_response_business_minutes == 25
