"""The API end to end: real commits, connections reused across requests."""

import pytest
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts.tests.factories import ClientUserFactory
from apps.core.rls import system_context

pytestmark = [pytest.mark.usefixtures("production_like"), pytest.mark.urls(__name__)]

PASSWORD = "password"  # The factory default.


class ContextView(APIView):
    """Reports the context and role a request runs with. Open to anyone."""

    permission_classes = [AllowAny]

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT app_role(), app_org_id(), app_user_id(), current_user, pg_backend_pid()"
            )
            role, org_id, user_id, db_role, pid = cursor.fetchone()
        return JsonResponse({"context": [role, org_id, user_id], "db_role": db_role, "pid": pid})


urlpatterns = [
    path("context/", ContextView.as_view()),
    path("api/", include("apps.accounts.urls")),
]


@pytest.fixture
def api():
    return APIClient()


def test_login_me_and_refresh(api):
    with system_context():
        user = ClientUserFactory()

    tokens = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()
    me = api.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    refreshed = api.post("/api/auth/token/refresh/", {"refresh": tokens["refresh"]})

    assert me.status_code == 200
    assert me.json()["organization"]["id"] == user.organization_id
    assert refreshed.status_code == 200
    with system_context():
        user.refresh_from_db()
        assert user.last_login is not None  # Committed by the login request.


def test_context_does_not_leak_to_the_next_request_on_the_same_connection(api):
    with system_context():
        user = ClientUserFactory()
    access = api.post("/api/auth/token/", {"email": user.email, "password": PASSWORD}).json()[
        "access"
    ]

    authenticated = api.get("/context/", HTTP_AUTHORIZATION=f"Bearer {access}").json()
    anonymous = api.get("/context/").json()

    assert authenticated["db_role"] == anonymous["db_role"] == "helpdesk_app"
    assert authenticated["pid"] == anonymous["pid"]  # Same server session (CONN_MAX_AGE).
    assert authenticated["context"] == ["client", user.organization_id, user.id]
    assert anonymous["context"] == [None, None, None]
