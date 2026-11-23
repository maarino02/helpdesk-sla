"""Ticket API end to end: real login, JWT, row-level security and commits.

Unlike apps/tickets/tests/test_api.py, every request here goes through
RlsJWTAuthentication, which sets the context of each request transaction.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.core.rls import system_context
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import Ticket, TicketEvent
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.usefixtures("production_like")

PASSWORD = "password"  # The factory default.


def logged_in(user):
    api = APIClient()
    tokens = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api


@pytest.fixture
def data():
    with system_context():
        SlaPolicyFactory(priority=Priority.HIGH)
        category = CategoryFactory()
        customer = ClientUserFactory()
        colleague = ClientUserFactory(organization=customer.organization)
        outsider = ClientUserFactory()
        agent = AgentUserFactory()
    return {
        "category": category,
        "customer": customer,
        "colleague": colleague,
        "outsider": outsider,
        "agent": agent,
    }


def open_ticket(api, category, title):
    return api.post(
        "/api/tickets/",
        {
            "title": title,
            "description": "Detalles.",
            "category": category.pk,
            "priority": Priority.HIGH,
        },
    )


def test_create_list_and_read_tickets(data):
    customer_api = logged_in(data["customer"])

    categories = customer_api.get("/api/categories/")
    first = open_ticket(customer_api, data["category"], "Primero")
    second = open_ticket(logged_in(data["colleague"]), data["category"], "Segundo")
    listed = customer_api.get("/api/tickets/")
    detail = customer_api.get(first["Location"])

    assert categories.json() == [{"id": data["category"].pk, "name": data["category"].name}]
    assert (first.status_code, second.status_code) == (201, 201)
    assert [first.json()["number"], second.json()["number"]] == [1, 2]
    # Newest first; the colleague's ticket is visible to the whole organization.
    assert [t["title"] for t in listed.json()["results"]] == ["Segundo", "Primero"]
    assert detail.json()["title"] == "Primero"
    with system_context():
        assert Ticket.objects.count() == 2
        assert TicketEvent.objects.count() == 2


def test_other_organizations_see_nothing(data):
    created = open_ticket(logged_in(data["customer"]), data["category"], "Privado")
    outsider_api = logged_in(data["outsider"])

    listed = outsider_api.get("/api/tickets/")
    detail = outsider_api.get(created["Location"])

    assert listed.json()["results"] == []
    assert detail.status_code == 404


def test_agents_see_every_organization_but_cannot_create(data):
    open_ticket(logged_in(data["customer"]), data["category"], "Del cliente")
    agent_api = logged_in(data["agent"])

    listed = agent_api.get("/api/tickets/")
    attempt = open_ticket(agent_api, data["category"], "Del agente")

    assert [t["title"] for t in listed.json()["results"]] == ["Del cliente"]
    assert attempt.status_code == 403


def test_full_lifecycle_through_the_api(data):
    created = open_ticket(logged_in(data["customer"]), data["category"], "Ciclo")
    url = f"{created['Location']}transitions/"
    agent_api = logged_in(data["agent"])
    customer_api = logged_in(data["customer"])
    steps = [
        (agent_api, "open", 200),
        (agent_api, "pending", 200),
        (customer_api, "open", 200),  # The client answers.
        (agent_api, "resolved", 200),
        (agent_api, "closed", 403),  # Only the client (or the system) closes.
        (customer_api, "open", 200),  # Reopened: the fix did not hold.
        (agent_api, "resolved", 200),
        (customer_api, "closed", 200),
        (customer_api, "open", 409),  # Closed is final.
    ]

    results = [
        (to_status, api.post(url, {"to_status": to_status}).status_code)
        for api, to_status, _ in steps
    ]

    assert results == [(to_status, code) for _, to_status, code in steps]
    final = customer_api.get(created["Location"]).json()
    assert final["status"] == "closed"
    assert final["assignee"]["id"] == data["agent"].pk
    with system_context():
        # created + assignee_changed + 7 successful status changes.
        assert TicketEvent.objects.count() == 9


def test_outsider_cannot_transition_another_organizations_ticket(data):
    created = open_ticket(logged_in(data["customer"]), data["category"], "Ajeno")

    response = logged_in(data["outsider"]).post(
        f"{created['Location']}transitions/", {"to_status": "open"}
    )

    assert response.status_code == 404


def test_admin_triages_a_ticket(data):
    with system_context():
        SlaPolicyFactory(priority=Priority.CRITICAL)
        admin = AdminUserFactory()
        new_category = CategoryFactory()
    created = open_ticket(logged_in(data["customer"]), data["category"], "Urgente")

    response = logged_in(admin).patch(
        created["Location"],
        {
            "assignee": data["agent"].pk,
            "priority": Priority.CRITICAL,
            "category": new_category.pk,
        },
        format="json",
    )
    as_client = logged_in(data["customer"]).get(created["Location"]).json()

    assert response.status_code == 200
    # The client sees the triage: assigned agent (name only) and new priority.
    assert as_client["assignee"] == {"id": data["agent"].pk, "full_name": data["agent"].full_name}
    assert as_client["priority"] == Priority.CRITICAL
    with system_context():
        types = set(TicketEvent.objects.values_list("event_type", flat=True))
    assert types == {"created", "assignee_changed", "priority_changed", "category_changed"}
