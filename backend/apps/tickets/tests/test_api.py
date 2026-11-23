"""Ticket API inside the test transaction.

force_authenticate skips RlsJWTAuthentication, so these requests keep the
system context of the test: row-level security sees every organization and only
the application's own filter scopes the results. That is what these tests
check; backend/tests/test_e2e_tickets.py runs the real authentication and RLS.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.tests.factories import CategoryFactory, TicketFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("tickets:list")


def detail_url(ticket):
    return reverse("tickets:detail", args=[ticket.pk])


def transitions_url(ticket):
    return reverse("tickets:transitions", args=[ticket.pk])


def api_as(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def ids(response):
    return {ticket["id"] for ticket in response.json()["results"]}


@pytest.fixture
def customer():
    return ClientUserFactory()


@pytest.fixture
def agent():
    return AgentUserFactory()


class TestList:
    def test_client_sees_only_their_organization(self, customer):
        own = TicketFactory(organization=customer.organization)
        colleague_ticket = TicketFactory(organization=customer.organization)
        TicketFactory()  # Another organization.

        response = api_as(customer).get(LIST_URL)

        assert response.status_code == 200
        # Decision: clients see every ticket of their organization, not only theirs.
        assert ids(response) == {own.pk, colleague_ticket.pk}

    @pytest.mark.parametrize("factory", [AgentUserFactory, AdminUserFactory])
    def test_staff_see_every_organization(self, factory):
        tickets = TicketFactory.create_batch(2)

        response = api_as(factory()).get(LIST_URL)

        assert ids(response) == {ticket.pk for ticket in tickets}

    def test_unauthenticated(self):
        assert APIClient().get(LIST_URL).status_code == 401

    def test_list_item_fields(self, customer):
        ticket = TicketFactory(organization=customer.organization)

        item = api_as(customer).get(LIST_URL).json()["results"][0]

        assert item["reference"] == ticket.reference
        assert item["category"] == {"id": ticket.category_id, "name": ticket.category.name}
        assert item["requester"] == {
            "id": ticket.requester_id,
            "full_name": ticket.requester.full_name,
        }
        assert item["assignee"] is None
        assert "description" not in item
        assert "email" not in str(item)

    def test_query_count_does_not_grow_with_the_tickets(self, agent):
        # No N+1: the relations come in the same query (select_related).
        TicketFactory.create_batch(2, assignee=AgentUserFactory())
        api = api_as(agent)
        with CaptureQueriesContext(connection) as few:
            api.get(LIST_URL)
        TicketFactory.create_batch(8, assignee=AgentUserFactory())
        with CaptureQueriesContext(connection) as many:
            api.get(LIST_URL)

        assert len(many) == len(few)


class TestFilters:
    def test_status_and_priority(self, agent):
        match = TicketFactory(priority=Priority.HIGH)
        TicketFactory(priority=Priority.LOW)
        TicketFactory(priority=Priority.HIGH, resolved=True)

        response = api_as(agent).get(LIST_URL, {"status": Status.NEW, "priority": Priority.HIGH})

        assert ids(response) == {match.pk}

    def test_assignee(self, agent):
        mine = TicketFactory(assignee=agent)
        unassigned = TicketFactory()
        colleague = AgentUserFactory()
        theirs = TicketFactory(assignee=colleague)
        api = api_as(agent)

        assert ids(api.get(LIST_URL, {"assignee": "me"})) == {mine.pk}
        assert ids(api.get(LIST_URL, {"assignee": "none"})) == {unassigned.pk}
        assert ids(api.get(LIST_URL, {"assignee": colleague.pk})) == {theirs.pk}

    @pytest.mark.parametrize(
        ("param", "value"),
        [("status", "closedd"), ("priority", "urgent"), ("assignee", "someone")],
    )
    def test_invalid_value_is_rejected_not_ignored(self, agent, param, value):
        response = api_as(agent).get(LIST_URL, {param: value})

        assert response.status_code == 400
        assert param in response.json()


class TestPagination:
    def test_pages_neither_repeat_nor_skip_tickets(self, agent):
        # Same created_at for all: only id orders them, so the tie-breaker is tested.
        now = timezone.now()
        tickets = TicketFactory.create_batch(30, created_at=now)
        api = api_as(agent)

        first = api.get(LIST_URL).json()
        second = api.get(first["next"]).json()

        assert len(first["results"]) == 25
        assert second["next"] is None
        seen = [t["id"] for t in first["results"] + second["results"]]
        assert seen == sorted((t.pk for t in tickets), reverse=True)

    def test_page_size_cannot_be_raised(self, agent):
        TicketFactory.create_batch(30)

        response = api_as(agent).get(LIST_URL, {"page_size": 1000})

        assert len(response.json()["results"]) == 25


class TestDetail:
    def test_detail_includes_the_description(self, customer):
        ticket = TicketFactory(organization=customer.organization)

        response = api_as(customer).get(detail_url(ticket))

        assert response.status_code == 200
        assert response.json()["description"] == ticket.description

    def test_another_organizations_ticket_is_not_found(self, customer):
        response = api_as(customer).get(detail_url(TicketFactory()))

        assert response.status_code == 404


class TestCreate:
    @pytest.fixture(autouse=True)
    def _policy(self):
        SlaPolicyFactory(priority=Priority.MEDIUM)

    @pytest.fixture
    def payload(self):
        return {
            "title": "No puedo entrar en el correo",
            "description": "Me dice que la contraseña es incorrecta.",
            "category": CategoryFactory().pk,
            "priority": Priority.MEDIUM,
        }

    def test_client_creates_a_ticket(self, customer, payload):
        response = api_as(customer).post(LIST_URL, payload)

        assert response.status_code == 201
        body = response.json()
        ticket = Ticket.objects.get(pk=body["id"])
        assert response["Location"] == detail_url(ticket)
        assert body["reference"] == f"{customer.organization.slug.upper()}-1"
        assert body["status"] == Status.NEW
        assert body["description"] == payload["description"]
        assert ticket.requester == customer

    def test_server_fields_are_ignored(self, customer, payload):
        payload |= {"status": Status.CLOSED, "number": 99, "assignee": AgentUserFactory().pk}

        body = api_as(customer).post(LIST_URL, payload).json()

        assert (body["status"], body["number"], body["assignee"]) == (Status.NEW, 1, None)

    @pytest.mark.parametrize("factory", [AgentUserFactory, AdminUserFactory])
    def test_staff_cannot_create(self, factory, payload):
        response = api_as(factory()).post(LIST_URL, payload)

        assert response.status_code == 403
        assert not Ticket.objects.exists()

    @pytest.mark.parametrize(
        ("field", "value"),
        [("priority", "urgent"), ("title", "   "), ("title", "x" * 201)],
    )
    def test_invalid_input(self, customer, payload, field, value):
        response = api_as(customer).post(LIST_URL, payload | {field: value})

        assert response.status_code == 400
        assert field in response.json()

    def test_inactive_category(self, customer, payload):
        payload["category"] = CategoryFactory(is_active=False).pk

        response = api_as(customer).post(LIST_URL, payload)

        assert response.status_code == 400
        assert "category" in response.json()

    def test_service_validation_error_becomes_a_400(self, customer, payload):
        # Only the service checks the organization: it reaches the API as Django's
        # ValidationError, translated by the exception handler.
        customer.organization.is_active = False
        customer.organization.save()

        response = api_as(customer).post(LIST_URL, payload)

        assert response.status_code == 400
        assert response.json() == {"non_field_errors": ["La organización no está activa."]}


def test_categories_lists_only_active_ones(customer):
    active = CategoryFactory(name="Hardware")
    CategoryFactory(name="Antigua", is_active=False)

    response = api_as(customer).get(reverse("tickets:categories"))

    assert response.status_code == 200
    assert response.json() == [{"id": active.pk, "name": "Hardware"}]


class TestTransitions:
    def test_agent_resolves_and_gets_the_updated_ticket(self, agent):
        ticket = TicketFactory(status=Status.OPEN, assignee=agent)

        response = api_as(agent).post(transitions_url(ticket), {"to_status": Status.RESOLVED})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == Status.RESOLVED
        assert body["resolved_at"] is not None
        assert body["assignee"]["id"] == agent.pk
        assert "description" in body  # The full ticket.

    def test_taking_a_new_ticket_shows_the_new_assignee(self, agent):
        response = api_as(agent).post(transitions_url(TicketFactory()), {"to_status": "open"})

        assert response.json()["assignee"] == {"id": agent.pk, "full_name": agent.full_name}

    def test_client_reopens_a_resolved_ticket(self, customer):
        ticket = TicketFactory(organization=customer.organization, resolved=True)

        response = api_as(customer).post(transitions_url(ticket), {"to_status": Status.OPEN})

        assert response.status_code == 200
        assert response.json()["status"] == Status.OPEN

    def test_transition_not_defined_is_a_409_with_the_current_status(self, agent):
        ticket = TicketFactory(closed=True)

        response = api_as(agent).post(transitions_url(ticket), {"to_status": Status.OPEN})

        assert response.status_code == 409
        assert response.json() == {
            "detail": "La incidencia está en estado «Cerrado» y no puede pasar a «Abierto».",
            "code": "transition_not_defined",
            "current_status": Status.CLOSED,
        }

    def test_transition_not_allowed_for_the_role_is_a_403(self, agent):
        ticket = TicketFactory(resolved=True)

        response = api_as(agent).post(transitions_url(ticket), {"to_status": Status.CLOSED})

        assert response.status_code == 403
        assert response.json() == {
            "detail": "No tienes permiso para pasar la incidencia de «Resuelto» a «Cerrado».",
            "code": "transition_not_allowed",
        }

    @pytest.mark.parametrize("body", [{}, {"to_status": "archived"}, {"to_status": ""}])
    def test_invalid_body_is_a_400(self, agent, body):
        response = api_as(agent).post(transitions_url(TicketFactory()), body)

        assert response.status_code == 400
        assert "to_status" in response.json()

    def test_another_organizations_ticket_is_not_found(self, customer):
        ticket = TicketFactory(resolved=True)

        response = api_as(customer).post(transitions_url(ticket), {"to_status": Status.OPEN})

        # 404 before any 403 or 409: the ticket's existence is not revealed.
        assert response.status_code == 404
        assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED

    def test_unauthenticated(self):
        response = APIClient().post(transitions_url(TicketFactory()), {"to_status": "open"})

        assert response.status_code == 401

    def test_rejected_transition_changes_nothing(self, agent):
        ticket = TicketFactory(resolved=True)

        api_as(agent).post(transitions_url(ticket), {"to_status": Status.CLOSED})

        assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED
        assert not TicketEvent.objects.filter(ticket=ticket).exists()

    def test_success_records_the_event_with_the_user(self, agent):
        ticket = TicketFactory(status=Status.OPEN, assignee=agent)

        api_as(agent).post(transitions_url(ticket), {"to_status": Status.PENDING})

        event = TicketEvent.objects.get(ticket=ticket, event_type=EventType.STATUS_CHANGED)
        assert (event.actor, event.from_value, event.to_value) == (agent, "open", "pending")

    def test_response_loads_no_relation_lazily(self, agent):
        ticket = TicketFactory(status=Status.OPEN, assignee=agent)

        with CaptureQueriesContext(connection) as queries:
            api_as(agent).post(transitions_url(ticket), {"to_status": Status.RESOLVED})

        # Without the final read through the selector, serializing the response
        # would run one SELECT per relation (requester, assignee, category...).
        lazy_loads = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].startswith(('SELECT "app_user"', 'SELECT "category"'))
        ]
        assert lazy_loads == []


class TestUpdate:
    @pytest.fixture(autouse=True)
    def _policies(self):
        for priority in (Priority.MEDIUM, Priority.HIGH):
            SlaPolicyFactory(priority=priority)

    def test_staff_assign_and_change_priority(self, agent):
        ticket = TicketFactory()

        response = api_as(agent).patch(
            detail_url(ticket), {"assignee": agent.pk, "priority": Priority.HIGH}, format="json"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["assignee"] == {"id": agent.pk, "full_name": agent.full_name}
        assert body["priority"] == Priority.HIGH
        assert "description" in body  # The full ticket.

    def test_null_unassigns(self, agent):
        ticket = TicketFactory(assignee=agent)

        response = api_as(agent).patch(detail_url(ticket), {"assignee": None}, format="json")

        assert response.json()["assignee"] is None

    def test_clients_cannot_patch(self, customer):
        ticket = TicketFactory(organization=customer.organization)

        response = api_as(customer).patch(
            detail_url(ticket), {"priority": Priority.HIGH}, format="json"
        )

        assert response.status_code == 403

    def test_status_is_rejected_with_a_hint(self, agent):
        response = api_as(agent).patch(
            detail_url(TicketFactory()), {"status": Status.CLOSED}, format="json"
        )

        assert response.status_code == 400
        assert response.json() == {"status": ["Usa /transitions/ para cambiar el estado."]}

    @pytest.mark.parametrize("field", ["title", "number", "resolution_due_at"])
    def test_non_editable_fields_are_rejected_not_ignored(self, agent, field):
        ticket = TicketFactory()

        response = api_as(agent).patch(detail_url(ticket), {field: "x"}, format="json")

        assert response.status_code == 400
        assert response.json() == {field: ["Este campo no se puede modificar."]}

    def test_client_cannot_be_the_assignee(self, agent, customer):
        response = api_as(agent).patch(
            detail_url(TicketFactory()), {"assignee": customer.pk}, format="json"
        )

        assert response.status_code == 400
        assert "assignee" in response.json()

    def test_closed_ticket_is_a_409(self, agent):
        response = api_as(agent).patch(
            detail_url(TicketFactory(closed=True)), {"assignee": agent.pk}, format="json"
        )

        assert response.status_code == 409
        assert response.json()["code"] == "ticket_closed"

    def test_priority_of_a_resolved_ticket_is_a_409(self, agent):
        response = api_as(agent).patch(
            detail_url(TicketFactory(resolved=True)), {"priority": Priority.HIGH}, format="json"
        )

        assert response.status_code == 409
        assert response.json()["code"] == "priority_locked"

    def test_put_is_not_allowed(self, agent):
        response = api_as(agent).put(detail_url(TicketFactory()), {}, format="json")

        assert response.status_code == 405

    def test_missing_ticket(self, agent):
        response = api_as(agent).patch(
            reverse("tickets:detail", args=[999999]), {"priority": "high"}, format="json"
        )

        assert response.status_code == 404
