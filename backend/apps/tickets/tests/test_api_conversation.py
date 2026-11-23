"""Comments and history endpoints inside the test transaction.

As in test_api.py, force_authenticate keeps the system context: row-level
security sees everything, so these tests check the application's own filters
(internal notes hidden from clients). backend/tests/test_e2e_tickets.py runs the
same flow with the real authentication and RLS.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.tickets.models import EventType, Status, TicketComment
from apps.tickets.tests.factories import (
    CategoryFactory,
    TicketCommentFactory,
    TicketEventFactory,
    TicketFactory,
)

pytestmark = pytest.mark.django_db


def api_as(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def comments_url(ticket):
    return reverse("tickets:comments", args=[ticket.pk])


def events_url(ticket):
    return reverse("tickets:events", args=[ticket.pk])


@pytest.fixture
def customer():
    return ClientUserFactory()


@pytest.fixture
def agent():
    return AgentUserFactory()


@pytest.fixture
def ticket(customer):
    return TicketFactory(organization=customer.organization, requester=customer)


class TestCommentList:
    def test_clients_never_see_internal_notes(self, ticket, customer, agent):
        public = TicketCommentFactory(ticket=ticket, author=agent, body="Lo miro.")
        TicketCommentFactory(ticket=ticket, author=agent, body="Driver roto.", is_internal=True)

        results = api_as(customer).get(comments_url(ticket)).json()["results"]

        assert [c["id"] for c in results] == [public.pk]

    def test_staff_see_internal_notes(self, ticket, agent):
        TicketCommentFactory(ticket=ticket, author=agent, is_internal=True)

        results = api_as(agent).get(comments_url(ticket)).json()["results"]

        assert [c["is_internal"] for c in results] == [True]

    def test_comment_fields(self, ticket, agent):
        TicketCommentFactory(ticket=ticket, author=agent, body="Hola")

        [comment] = api_as(agent).get(comments_url(ticket)).json()["results"]

        assert comment["author"] == {"id": agent.pk, "full_name": agent.full_name, "role": "agent"}
        assert comment["body"] == "Hola"
        assert "email" not in str(comment)

    def test_oldest_first_across_pages(self, ticket, agent):
        comments = TicketCommentFactory.create_batch(55, ticket=ticket, author=agent)
        api = api_as(agent)

        first = api.get(comments_url(ticket)).json()
        second = api.get(first["next"]).json()

        seen = [c["id"] for c in first["results"] + second["results"]]
        assert len(first["results"]) == 50
        assert seen == [c.pk for c in comments]

    def test_another_organizations_ticket_is_not_found(self, customer):
        assert api_as(customer).get(comments_url(TicketFactory())).status_code == 404

    def test_query_count_does_not_grow_with_the_comments(self, ticket, agent):
        TicketCommentFactory.create_batch(2, ticket=ticket, author=AgentUserFactory())
        api = api_as(agent)
        with CaptureQueriesContext(connection) as few:
            api.get(comments_url(ticket))
        TicketCommentFactory.create_batch(8, ticket=ticket, author=AgentUserFactory())
        with CaptureQueriesContext(connection) as many:
            api.get(comments_url(ticket))

        assert len(many) == len(few)


class TestCommentCreate:
    def test_client_comments(self, ticket, customer):
        response = api_as(customer).post(comments_url(ticket), {"body": "¿Hay novedades?"})

        assert response.status_code == 201
        body = response.json()
        assert body["author"]["id"] == customer.pk
        assert body["is_internal"] is False
        assert TicketComment.objects.get(pk=body["id"]).ticket == ticket

    def test_agent_reply_takes_the_ticket(self, ticket, agent):
        api = api_as(agent)

        api.post(comments_url(ticket), {"body": "Lo miro."})
        updated = api.get(reverse("tickets:detail", args=[ticket.pk])).json()

        assert updated["status"] == Status.OPEN
        assert updated["assignee"]["id"] == agent.pk
        assert updated["first_response_at"] is not None

    def test_agent_writes_an_internal_note(self, ticket, agent):
        response = api_as(agent).post(comments_url(ticket), {"body": "Ojo", "is_internal": True})

        assert response.status_code == 201
        assert response.json()["is_internal"] is True

    def test_client_internal_note_is_a_403(self, ticket, customer):
        response = api_as(customer).post(
            comments_url(ticket), {"body": "Secreto", "is_internal": True}
        )

        assert response.status_code == 403
        assert response.json()["code"] == "internal_notes_staff_only"
        assert not TicketComment.objects.exists()

    def test_closed_ticket_is_a_409(self, customer):
        ticket = TicketFactory(organization=customer.organization, closed=True)

        response = api_as(customer).post(comments_url(ticket), {"body": "Hola"})

        assert response.status_code == 409
        assert response.json()["code"] == "comments_closed"

    @pytest.mark.parametrize("body", [{}, {"body": ""}, {"body": "   "}])
    def test_empty_body_is_a_400(self, ticket, customer, body):
        response = api_as(customer).post(comments_url(ticket), body)

        assert response.status_code == 400
        assert "body" in response.json()

    def test_another_organizations_ticket_is_not_found(self, customer):
        response = api_as(customer).post(comments_url(TicketFactory()), {"body": "Hola"})

        assert response.status_code == 404


class TestEvents:
    def test_readable_values(self, ticket, agent):
        old_category, new_category = ticket.category, CategoryFactory(name="Redes")
        rows = [
            (EventType.CREATED, None, None),
            (EventType.STATUS_CHANGED, "new", "open"),
            (EventType.ASSIGNEE_CHANGED, None, str(agent.pk)),
            (EventType.PRIORITY_CHANGED, Priority.MEDIUM, Priority.HIGH),
            (EventType.CATEGORY_CHANGED, str(old_category.pk), str(new_category.pk)),
        ]
        for event_type, from_value, to_value in rows:
            TicketEventFactory(
                ticket=ticket,
                actor=agent,
                event_type=event_type,
                from_value=from_value,
                to_value=to_value,
            )

        results = api_as(agent).get(events_url(ticket)).json()["results"]

        displays = [(e["event_type"], e["from_display"], e["to_display"]) for e in results]
        assert displays == [
            ("created", None, None),
            ("status_changed", "Nuevo", "Abierto"),
            ("assignee_changed", None, agent.full_name),
            ("priority_changed", "Media", "Alta"),
            ("category_changed", old_category.name, "Redes"),
        ]
        assert results[1]["actor"] == {"id": agent.pk, "full_name": agent.full_name}

    def test_clients_see_the_history(self, ticket, customer):
        TicketEventFactory(ticket=ticket, actor=None)

        response = api_as(customer).get(events_url(ticket))

        assert response.status_code == 200
        assert response.json()["results"][0]["actor"] is None  # A system event.

    def test_another_organizations_ticket_is_not_found(self, customer):
        assert api_as(customer).get(events_url(TicketFactory())).status_code == 404

    def test_query_count_does_not_grow_with_the_events(self, ticket, agent):
        def add_assignments(n):
            for _ in range(n):
                TicketEventFactory(
                    ticket=ticket,
                    actor=AgentUserFactory(),
                    event_type=EventType.ASSIGNEE_CHANGED,
                    to_value=str(AgentUserFactory().pk),
                )

        api = api_as(agent)
        add_assignments(2)
        with CaptureQueriesContext(connection) as few:
            api.get(events_url(ticket))
        add_assignments(8)
        with CaptureQueriesContext(connection) as many:
            api.get(events_url(ticket))

        # Names are resolved in bulk, one query per kind, not one per event.
        assert len(many) == len(few)
