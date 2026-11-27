"""GET /api/organizations/: the clients the dashboard can be filtered by."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db

URL = reverse("organizations:list")


def api_as(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_every_client_active_ones_first_by_name():
    beta = OrganizationFactory(name="Beta")
    acme = OrganizationFactory(name="Acme")
    zeta = OrganizationFactory(name="Zeta", is_active=False)
    aloha = OrganizationFactory(name="Aloha", is_active=False)

    response = api_as(AgentUserFactory()).get(URL)

    assert response.status_code == 200
    assert response.json() == [
        {"id": acme.pk, "name": "Acme", "is_active": True},
        {"id": beta.pk, "name": "Beta", "is_active": True},
        {"id": aloha.pk, "name": "Aloha", "is_active": False},
        {"id": zeta.pk, "name": "Zeta", "is_active": False},
    ]


def test_admins_can_list_them_too():
    assert api_as(AdminUserFactory()).get(URL).status_code == 200


def test_clients_are_forbidden():
    assert api_as(ClientUserFactory()).get(URL).status_code == 403


def test_anonymous_requests_are_rejected():
    assert APIClient().get(URL).status_code == 401


def test_every_listed_client_is_accepted_by_the_dashboard():
    # The list and the analytics filter must agree: choosing a name from the
    # select must never be rejected, a former client's included.
    OrganizationFactory()
    OrganizationFactory(is_active=False)
    api = api_as(AgentUserFactory())
    metric = reverse("analytics:sla-compliance")

    for organization in api.get(URL).json():
        response = api.get(metric, {"organization": organization["id"]})
        assert response.status_code == 200, response.json()
        assert response.json()["period"]["organization"] == organization["id"]
