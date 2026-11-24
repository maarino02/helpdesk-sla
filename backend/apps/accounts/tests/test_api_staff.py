"""GET /api/staff/: whom a ticket can be assigned to."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("accounts:staff")


def api_as(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_active_agents_and_admins_by_name():
    bea = AgentUserFactory(full_name="Bea Soto")
    ana = AdminUserFactory(full_name="Ana Ruiz")
    AgentUserFactory(full_name="Carla Gone", is_active=False)
    ClientUserFactory(full_name="Aaron Cliente")

    response = api_as(bea).get(URL)

    assert response.status_code == 200
    assert response.json() == [
        {"id": ana.pk, "full_name": "Ana Ruiz", "role": "admin"},
        {"id": bea.pk, "full_name": "Bea Soto", "role": "agent"},
    ]


def test_clients_are_forbidden():
    assert api_as(ClientUserFactory()).get(URL).status_code == 403


def test_everyone_listed_can_be_assigned():
    # The list and the triage rule (_validate_assignee) must agree: choosing a
    # name from the select must never be rejected.
    agent = AgentUserFactory()
    AdminUserFactory()
    ticket = TicketFactory()
    api = api_as(agent)

    for member in api.get(URL).json():
        response = api.patch(
            reverse("tickets:detail", args=[ticket.pk]), {"assignee": member["id"]}, format="json"
        )
        assert response.status_code == 200, response.json()
        assert response.json()["assignee"]["id"] == member["id"]
