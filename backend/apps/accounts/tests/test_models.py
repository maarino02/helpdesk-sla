import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import (
    AdminUserFactory,
    AgentUserFactory,
    ClientUserFactory,
)
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


def assert_violates(constraint_name, factory, **kwargs):
    # The database must reject the row; the savepoint keeps the test transaction usable.
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


def test_user_is_stored_in_app_user_table():
    user = AgentUserFactory()

    # Query the table by name to check the real schema, not just the model's Meta.
    with connection.cursor() as cursor:
        cursor.execute("SELECT email FROM app_user WHERE id = %s", [user.pk])
        assert cursor.fetchone() == (user.email,)
    assert user.check_password("password")


def test_database_defaults_apply_on_insert():
    user = AgentUserFactory()

    assert user.is_active is True
    assert user.is_staff is False
    assert user.date_joined is not None


# Email


def test_create_user_lowercases_the_whole_email():
    user = User.objects.create_user(
        "  Ana.Garcia@ACME.com ", "password", full_name="Ana", role=Role.AGENT
    )

    assert user.email == "ana.garcia@acme.com"


def test_clean_lowercases_the_email():
    # Forms such as the admin call full_clean(), not the manager.
    user = User(email="Ana@Acme.com", full_name="Ana", role=Role.AGENT)
    user.set_password("password")

    user.full_clean()

    assert user.email == "ana@acme.com"


def test_email_is_unique():
    existing = AgentUserFactory()

    assert_violates("app_user_unique_email", AgentUserFactory, email=existing.email)


def test_email_uniqueness_ignores_case_through_the_manager():
    User.objects.create_user("ana@acme.com", "password", full_name="Ana", role=Role.AGENT)

    with pytest.raises(IntegrityError, match="app_user_unique_email"), transaction.atomic():
        User.objects.create_user("ANA@acme.com", "password", full_name="Ana", role=Role.AGENT)


def test_uppercase_email_is_rejected_by_the_database():
    # Bypassing the normalization (factory sets the field directly) hits the CHECK.
    assert_violates("app_user_email_lowercase", AgentUserFactory, email="Ana@acme.com")


def test_empty_email_is_rejected_by_the_database():
    assert_violates("app_user_email_not_empty", AgentUserFactory, email="")


def test_login_ignores_email_case():
    user = User.objects.create_user("ana@acme.com", "password", full_name="Ana", role=Role.AGENT)

    assert authenticate(email="ANA@Acme.com", password="password") == user


# Role and organization


def test_invalid_role_is_rejected():
    assert_violates("app_user_role_valid", AgentUserFactory, role="superhero")


def test_client_must_have_an_organization():
    assert_violates("app_user_role_organization_consistent", ClientUserFactory, organization=None)


@pytest.mark.parametrize("factory", [AgentUserFactory, AdminUserFactory])
def test_staff_roles_cannot_have_an_organization(factory):
    assert_violates(
        "app_user_role_organization_consistent",
        factory,
        organization=OrganizationFactory(),
    )


def test_role_organization_rule_is_also_validated_by_full_clean():
    # Model.full_clean() runs the CHECK in Python, so the admin shows a form error
    # instead of a 500.
    user = User(email="ana@acme.com", full_name="Ana", role=Role.CLIENT)
    user.set_password("password")

    with pytest.raises(ValidationError, match="deben pertenecer a una organización"):
        user.full_clean()


def test_client_cannot_be_staff():
    assert_violates("app_user_client_not_staff", ClientUserFactory, is_staff=True)


@pytest.mark.parametrize("factory", [AgentUserFactory, AdminUserFactory])
def test_staff_roles_can_access_the_admin(factory):
    assert factory(is_staff=True).is_staff is True


def test_organization_with_users_cannot_be_deleted():
    user = ClientUserFactory()

    with pytest.raises(ProtectedError):
        user.organization.delete()


# Manager


def test_create_superuser_is_an_admin_without_organization():
    user = User.objects.create_superuser("root@example.com", "password", full_name="Root")

    assert user.role == Role.ADMIN
    assert user.organization is None
    assert user.is_staff is True
    assert user.is_superuser is True


def test_create_superuser_rejects_other_roles():
    with pytest.raises(ValueError, match="admin role"):
        User.objects.create_superuser(
            "root@example.com", "password", full_name="Root", role=Role.AGENT
        )


def test_create_user_requires_an_email():
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user("", "password", full_name="Ana", role=Role.AGENT)
