"""Login and session lookups through RlsModelBackend.

Each test clears the context first, as a real request starts with none.
"""

import pytest
from django.contrib.auth import authenticate

from apps.accounts.backends import RlsModelBackend
from apps.accounts.tests.factories import ClientUserFactory
from apps.core import rls
from tests.rls import fetchone

pytestmark = pytest.mark.django_db

CONTEXT = "SELECT app_role(), app_org_id(), app_user_id(), app_login_email()"
NO_CONTEXT = (None, None, None, None)
PASSWORD = "password"  # The factory default.


def test_valid_credentials_return_the_user_with_their_context():
    user = ClientUserFactory()
    rls.clear_context()

    assert authenticate(username=user.email, password=PASSWORD) == user
    # The login context is gone: the user's own context can update last_login.
    assert fetchone(CONTEXT) == ("client", user.organization_id, user.id, None)


def test_email_is_case_insensitive():
    user = ClientUserFactory(email="ana@example.com")
    rls.clear_context()

    assert authenticate(username=" Ana@Example.COM", password=PASSWORD) == user


@pytest.mark.parametrize(
    ("email", "password"),
    [("ana@example.com", "wrong"), ("nobody@example.com", PASSWORD)],
    ids=["wrong-password", "unknown-email"],
)
def test_failed_login_returns_none_and_no_context(email, password):
    ClientUserFactory(email="ana@example.com")
    rls.clear_context()

    assert authenticate(username=email, password=password) is None
    assert fetchone(CONTEXT) == NO_CONTEXT


def test_inactive_user_cannot_log_in():
    user = ClientUserFactory(is_active=False)
    rls.clear_context()

    assert authenticate(username=user.email, password=PASSWORD) is None
    assert fetchone(CONTEXT) == NO_CONTEXT


def test_outdated_hash_is_upgraded_in_the_users_own_context(settings):
    user = ClientUserFactory()  # Hashed with MD5, the test hasher.
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.ScryptPasswordHasher",
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    rls.clear_context()

    authenticated = authenticate(username=user.email, password=PASSWORD)

    # Saved after activate_user: in the read-only login context it would fail.
    assert authenticated.password.startswith("scrypt$")
    user.refresh_from_db()
    assert user.password == authenticated.password


def test_get_user_loads_the_session_user_with_their_context():
    user = ClientUserFactory()
    rls.clear_context()

    assert RlsModelBackend().get_user(user.id) == user
    assert fetchone(CONTEXT) == ("client", user.organization_id, user.id, None)


def test_get_user_rejects_inactive_users():
    user = ClientUserFactory(is_active=False)
    rls.clear_context()

    assert RlsModelBackend().get_user(user.id) is None


@pytest.mark.django_db(transaction=True)
def test_login_works_outside_a_transaction():
    # As in the shell: no request transaction around the call.
    with rls.system_context():
        user = ClientUserFactory()

    assert authenticate(username=user.email, password=PASSWORD) == user
