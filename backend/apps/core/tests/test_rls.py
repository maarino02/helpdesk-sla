import pytest
from django.db import transaction

from apps.accounts.tests.factories import ClientUserFactory
from apps.core import rls
from tests.rls import fetchone

pytestmark = pytest.mark.django_db

CONTEXT = "SELECT app_role(), app_org_id(), app_user_id(), app_login_email()"


@pytest.mark.django_db(transaction=True)
def test_set_context_refuses_to_run_outside_a_transaction():
    # In autocommit the values would vanish after this statement.
    with pytest.raises(RuntimeError, match="open transaction"):
        rls.set_context(role="system")


def test_set_context_is_a_single_query(django_assert_num_queries):
    with django_assert_num_queries(1):
        rls.set_context(role="client", org_id=1, user_id=2)


def test_values_not_given_are_cleared():
    rls.login_context("ana@example.com")
    rls.set_context(role="client", org_id=1, user_id=2)

    assert fetchone(CONTEXT) == ("client", 1, 2, None)


def test_login_context_normalizes_the_email():
    rls.login_context("  Ana@Example.COM ")

    assert fetchone(CONTEXT) == ("login", None, None, "ana@example.com")


def test_activate_user_widens_to_the_role_and_organization_of_the_row():
    user = ClientUserFactory()
    rls.clear_context()

    assert rls.activate_user(user.id) == user
    assert fetchone(CONTEXT) == ("client", user.organization_id, user.id, None)


def test_activate_user_rejects_inactive_users():
    user = ClientUserFactory(is_active=False)
    rls.clear_context()

    assert rls.activate_user(user.id) is None
    assert fetchone(CONTEXT) == (None, None, None, None)


@pytest.mark.parametrize("user_id", [999_999, "abc", None])
def test_activate_user_rejects_unknown_or_malformed_ids(user_id):
    rls.clear_context()

    assert rls.activate_user(user_id) is None
    assert fetchone(CONTEXT) == (None, None, None, None)
    # Queries keep working: a malformed id never reaches the bigint cast.
    assert fetchone("SELECT count(*) FROM ticket") == (0,)


def test_system_context_sees_every_organization():
    rls.clear_context()

    with rls.system_context():
        assert fetchone("SELECT app_role()") == ("system",)


def test_system_context_cannot_be_used_inside_a_request_transaction():
    # Every request runs inside transaction.atomic (ATOMIC_REQUESTS). The test's
    # own transaction is exempt, so this inner block plays the request.
    with pytest.raises(RuntimeError, match="durable"), transaction.atomic(), rls.system_context():
        pass
