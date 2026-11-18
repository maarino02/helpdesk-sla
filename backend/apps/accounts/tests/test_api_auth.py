"""Login, refresh and /api/me/ over HTTP, with the real row-level security context.

Each test clears the context before the request, as a real request starts with
none; assertions about the database switch to the system context afterwards.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path, reverse
from rest_framework.test import APIClient
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import Role, User
from apps.accounts.serializers import LoginSerializer
from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.core import rls

pytestmark = pytest.mark.django_db

PASSWORD = "password"  # The factory default.
LOGIN_URL = reverse("accounts:token")
REFRESH_URL = reverse("accounts:token-refresh")
ME_URL = reverse("accounts:me")


@pytest.fixture
def api():
    return APIClient()


def bearer(user_or_token):
    token = user_or_token if isinstance(user_or_token, str) else AccessToken.for_user(user_or_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# Login


def test_login_returns_both_tokens_and_records_last_login(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = api.post(LOGIN_URL, {"email": user.email, "password": PASSWORD})

    assert response.status_code == 200
    assert set(response.json()) == {"access", "refresh"}
    rls.set_context(role="system")
    user.refresh_from_db()
    assert user.last_login is not None


def test_login_accepts_the_email_in_any_case(api):
    ClientUserFactory(email="ana@example.com")
    rls.clear_context()

    response = api.post(LOGIN_URL, {"email": "Ana@Example.COM", "password": PASSWORD})

    assert response.status_code == 200


@pytest.mark.parametrize("case", ["wrong-password", "unknown-email", "inactive"])
def test_failed_logins_look_the_same(api, case):
    ClientUserFactory(email="ana@example.com", is_active=case != "inactive")
    credentials = {
        "wrong-password": ("ana@example.com", "wrong"),
        "unknown-email": ("nobody@example.com", PASSWORD),
        "inactive": ("ana@example.com", PASSWORD),
    }[case]
    rls.clear_context()

    response = api.post(LOGIN_URL, {"email": credentials[0], "password": credentials[1]})

    # The same answer in every case: the response must not reveal which emails
    # exist or which accounts are inactive.
    assert response.status_code == 401
    assert response.json()["detail"] == str(
        LoginSerializer.default_error_messages["no_active_account"]
    )


def test_login_is_throttled_per_client_address(api):
    ClientUserFactory(email="ana@example.com")
    rls.clear_context()

    statuses = [
        # A new X-Forwarded-For per attempt must not reset the count.
        api.post(
            LOGIN_URL,
            {"email": "ana@example.com", "password": "wrong"},
            HTTP_X_FORWARDED_FOR=f"10.0.0.{attempt}",
        ).status_code
        for attempt in range(11)
    ]

    assert statuses == [401] * 10 + [429]


# Refresh


def test_refresh_returns_a_new_access_token(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = api.post(REFRESH_URL, {"refresh": str(RefreshToken.for_user(user))})

    assert response.status_code == 200
    assert set(response.json()) == {"access"}  # No rotation (ADR-0004).


def test_refresh_is_refused_once_the_user_is_deactivated(api):
    user = ClientUserFactory()
    refresh = str(RefreshToken.for_user(user))
    User.objects.filter(pk=user.pk).update(is_active=False)
    rls.clear_context()

    response = api.post(REFRESH_URL, {"refresh": refresh})

    assert response.status_code == 401


def test_refresh_rejects_a_malformed_token(api):
    rls.clear_context()

    assert api.post(REFRESH_URL, {"refresh": "not-a-token"}).status_code == 401


# /api/me/


def test_me_returns_the_user_without_the_password(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = api.get(ME_URL, **bearer(user))

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": "client",
        "organization": {"id": user.organization_id, "name": user.organization.name},
    }


def test_me_reflects_role_changes_made_after_the_token_was_issued(api):
    user = ClientUserFactory()
    token = str(AccessToken.for_user(user))
    User.objects.filter(pk=user.pk).update(role=Role.AGENT, organization=None)
    rls.clear_context()

    response = api.get(ME_URL, **bearer(token))

    assert (response.json()["role"], response.json()["organization"]) == ("agent", None)


def test_deactivated_user_is_rejected_with_a_valid_token(api):
    user = ClientUserFactory()
    token = str(AccessToken.for_user(user))
    User.objects.filter(pk=user.pk).update(is_active=False)
    rls.clear_context()

    assert api.get(ME_URL, **bearer(token)).status_code == 401


def expired_token(user):
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=-timedelta(seconds=1))
    return str(token)


def tampered_token(user):
    # Another user's id with the original signature: the signature check fails.
    header, _, signature = str(AccessToken.for_user(user)).split(".")
    _, other_payload, _ = str(AccessToken.for_user(AgentUserFactory())).split(".")
    return f"{header}.{other_payload}.{signature}"


@pytest.mark.parametrize(
    "headers",
    [
        lambda user: {},
        lambda user: bearer(expired_token(user)),
        lambda user: bearer(tampered_token(user)),
    ],
    ids=["no-token", "expired", "tampered"],
)
def test_me_requires_a_valid_token(api, headers):
    user = ClientUserFactory()
    request_headers = headers(user)
    rls.clear_context()

    response = api.get(ME_URL, **request_headers)

    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


# The context inside an authenticated request


class ContextView(APIView):
    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT app_role(), app_org_id(), app_user_id(), app_login_email()")
            return JsonResponse({"context": list(cursor.fetchone())})


urlpatterns = [
    path("context/", ContextView.as_view()),
    path("api/", include("apps.accounts.urls")),
]


@pytest.mark.urls(__name__)
def test_authenticated_request_runs_with_the_users_context(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = api.get("/context/", **bearer(user))

    assert response.json() == {"context": ["client", user.organization_id, user.id, None]}
