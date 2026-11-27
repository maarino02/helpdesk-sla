"""GET /api/organizations/ under the real row-level security context.

The other tests run with the system context, which sees every row whatever the
policies say. Here the request runs as helpdesk_app with the context its JWT
sets: if the policy on organization stopped letting staff see every client, the
dashboard's select would come out empty while those tests still passed.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.core.rls import system_context
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.usefixtures("production_like")

PASSWORD = "password"  # The factory default.


def logged_in(user):
    api = APIClient()
    tokens = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api


def test_staff_see_every_client_and_clients_none():
    with system_context():
        customer = ClientUserFactory()
        other = OrganizationFactory(is_active=False)
        agent = AgentUserFactory()

    as_agent = logged_in(agent).get("/api/organizations/")
    as_client = logged_in(customer).get("/api/organizations/")

    assert as_agent.status_code == 200
    assert {row["id"] for row in as_agent.json()} == {customer.organization_id, other.pk}
    assert as_client.status_code == 403
