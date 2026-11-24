"""Browser sessions (ADR-0006): access token in the body, refresh token in a cookie.

As in test_api_auth.py, each test clears the row-level security context before
the request, as a real request starts with none.
"""

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.accounts.tests.factories import ClientUserFactory
from apps.core import rls

pytestmark = pytest.mark.django_db

PASSWORD = "password"  # The factory default.
LOGIN_URL = reverse("accounts:session")
REFRESH_URL = reverse("accounts:session-refresh")
LOGOUT_URL = reverse("accounts:session-logout")
ME_URL = reverse("accounts:me")
COOKIE = settings.REFRESH_COOKIE_NAME


@pytest.fixture
def api():
    return APIClient()


def login(api, email, password=PASSWORD, **extra):
    return api.post(LOGIN_URL, {"email": email, "password": password}, format="json", **extra)


def refresh(api):
    return api.post(REFRESH_URL, {}, format="json")


def me(api, access):
    return api.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {access}")


# Login


def test_login_returns_the_access_token_and_sets_the_refresh_cookie(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = login(api, user.email)

    assert response.status_code == 200
    assert set(response.json()) == {"access"}  # The refresh token never in the body.
    assert me(api, response.json()["access"]).json()["id"] == user.pk
    cookie = response.cookies[COOKIE]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == settings.REFRESH_COOKIE_PATH
    assert cookie["secure"] is True  # Only dev.py turns it off.
    assert cookie["max-age"] == 24 * 60 * 60  # The refresh token's lifetime.


def test_a_failed_login_sets_no_cookie(api):
    user = ClientUserFactory()
    rls.clear_context()

    response = login(api, user.email, "wrong")

    assert response.status_code == 401
    assert COOKIE not in response.cookies


@pytest.mark.parametrize("url", [LOGIN_URL, REFRESH_URL, LOGOUT_URL])
@pytest.mark.parametrize("content_type", ["application/x-www-form-urlencoded", "text/plain", None])
def test_only_json_is_accepted(api, url, content_type):
    # What an HTML form on another site can send: form or plain-text bodies, or
    # an empty body. JSON from another origin would need a CORS preflight.
    user = ClientUserFactory()
    api.cookies[COOKIE] = str(RefreshToken.for_user(user))
    rls.clear_context()

    if content_type is None:
        response = api.post(url)
    else:
        body = f"email={user.email}&password={PASSWORD}"
        response = api.generic("POST", url, body, content_type=content_type)

    assert response.status_code == 415
    assert COOKIE not in response.cookies  # Neither set nor removed.


def test_session_login_shares_the_throttle_of_the_token_login(api):
    user = ClientUserFactory()
    rls.clear_context()

    for _ in range(10):
        api.post(reverse("accounts:token"), {"email": user.email, "password": "wrong"})

    assert login(api, user.email).status_code == 429


# Refresh


def test_refresh_reads_the_cookie(api):
    user = ClientUserFactory()
    rls.clear_context()
    login(api, user.email)  # The client keeps the cookie, as a browser would.

    response = refresh(api)

    assert response.status_code == 200
    assert set(response.json()) == {"access"}
    assert me(api, response.json()["access"]).json()["id"] == user.pk


def test_refresh_without_a_session(api):
    rls.clear_context()

    response = refresh(api)

    assert response.status_code == 401
    assert response.json()["code"] == "no_session"


def test_refresh_rejects_a_tampered_cookie(api):
    api.cookies[COOKIE] = "not-a-token"
    rls.clear_context()

    assert refresh(api).status_code == 401


def test_refresh_is_refused_once_the_user_is_deactivated(api):
    user = ClientUserFactory()
    api.cookies[COOKIE] = str(RefreshToken.for_user(user))
    User.objects.filter(pk=user.pk).update(is_active=False)
    rls.clear_context()

    assert refresh(api).status_code == 401


# Logout


def test_logout_removes_the_cookie(api):
    user = ClientUserFactory()
    rls.clear_context()
    login(api, user.email)

    response = api.post(LOGOUT_URL, {}, format="json")

    assert response.status_code == 204
    cookie = response.cookies[COOKIE]
    # Removing means the same name and path, an empty value and an expiry in the past.
    assert (cookie.value, cookie["path"], cookie["max-age"]) == (
        "",
        settings.REFRESH_COOKIE_PATH,
        0,
    )
    assert refresh(api).json()["code"] == "no_session"


def test_the_cookie_path_covers_the_three_views():
    # The cookie is only sent to URLs under its path: if a view moved out of it,
    # the browser would stop sending the cookie there.
    for url in (LOGIN_URL, REFRESH_URL, LOGOUT_URL):
        assert url.startswith(settings.REFRESH_COOKIE_PATH)
