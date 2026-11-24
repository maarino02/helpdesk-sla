"""Analytics queries under the real row-level security context.

The SQL runs on the request's connection: a client's query sees only that
client's rows, even a query with no organization filter at all.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.analytics import queries
from apps.analytics.tests import probe
from apps.core.rls import system_context
from apps.tickets.tests.factories import TicketFactory

pytestmark = [
    pytest.mark.usefixtures("production_like"),
    pytest.mark.urls("apps.analytics.tests.probe"),
]

PASSWORD = "password"  # The factory default.
PERIOD = {"start": "2000-01-01", "end": "2002-12-30"}


@pytest.fixture(autouse=True)
def _probe_queries(tmp_path, monkeypatch):
    probe.install(tmp_path, monkeypatch)
    yield
    queries.load.cache_clear()


def logged_in(user):
    api = APIClient()
    tokens = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api


@pytest.fixture
def two_clients():
    with system_context():
        customer = ClientUserFactory()
        TicketFactory(organization=customer.organization, created_at="2001-06-01T10:00:00Z")
        TicketFactory(created_at="2001-06-01T10:00:00Z")  # Another organization.
        agent = AgentUserFactory()
    return customer, agent


def test_a_query_without_organization_filter_still_sees_only_the_clients_rows(two_clients):
    customer, agent = two_clients

    as_client = logged_in(customer).get("/unfiltered/", PERIOD).json()
    as_agent = logged_in(agent).get("/unfiltered/", PERIOD).json()

    assert as_client["results"] == [{"tickets": 1}]
    assert as_agent["results"] == [{"tickets": 2}]
