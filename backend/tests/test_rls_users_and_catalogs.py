"""Row-level security on app_user, organization and the catalogs.

Rows are created with the system context (backend/conftest.py); each test then
switches to the context under test.
"""

import pytest
from django.db import ProgrammingError, transaction
from django.db.models import F

from apps.accounts.models import User
from apps.accounts.tests.factories import (
    AdminUserFactory,
    AgentUserFactory,
    ClientUserFactory,
)
from apps.organizations.models import Organization
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import BusinessCalendar, BusinessHours, Holiday, Priority, SlaPolicy
from apps.sla.tests.factories import (
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
)
from apps.tickets.models import Category
from apps.tickets.tests.factories import CategoryFactory
from tests.rls import set_context

pytestmark = pytest.mark.django_db

RLS_ERROR = "row-level security"
COLUMN_GUARD_ERROR = "may only change"


def visible_ids(model):
    return set(model.objects.values_list("id", flat=True))


def rejected(match):
    """Expect the statement to fail; the savepoint keeps the test transaction usable."""
    return pytest.raises(ProgrammingError, match=match)


# Login and identification


def test_login_context_reads_only_the_row_with_that_email():
    user = ClientUserFactory()
    ClientUserFactory(organization=user.organization)
    set_context("login", login_email=user.email)

    assert visible_ids(User) == {user.id}


def test_login_context_cannot_write():
    user = ClientUserFactory()
    set_context("login", login_email=user.email)

    # No UPDATE policy matches: the row is silently not updated.
    assert User.objects.filter(pk=user.pk).update(full_name="Changed") == 0


def test_login_context_sees_nothing_without_an_email():
    ClientUserFactory()
    set_context("login")

    assert User.objects.count() == 0


def test_user_id_alone_reads_only_the_own_row():
    user = ClientUserFactory()
    AgentUserFactory()
    set_context(user_id=user.id)

    assert list(User.objects.values_list("id", "role", "organization_id")) == [
        (user.id, "client", user.organization_id)
    ]


# Users directory


def test_client_sees_users_of_their_organization_and_staff():
    client = ClientUserFactory()
    colleague = ClientUserFactory(organization=client.organization)
    ClientUserFactory()  # Another organization.
    agent = AgentUserFactory()
    admin = AdminUserFactory()
    set_context("client", client.organization_id, user_id=client.id)

    assert visible_ids(User) == {client.id, colleague.id, agent.id, admin.id}


@pytest.mark.parametrize("role", ["agent", "admin", "system"])
def test_staff_and_system_see_every_user(role):
    ClientUserFactory.create_batch(2)
    AgentUserFactory()
    set_context(role)

    assert User.objects.count() == 3


# Users: column guard


def test_user_can_update_password_last_login_and_name():
    client = ClientUserFactory()
    set_context("client", client.organization_id, user_id=client.id)

    client.set_password("new-password")
    client.full_name = "New name"
    client.save()  # A full save rewrites every column with its current value.
    User.objects.filter(pk=client.pk).update(last_login=F("date_joined"))

    client.refresh_from_db()
    assert client.check_password("new-password")
    assert client.full_name == "New name"


def test_agent_cannot_make_themselves_superuser():
    agent = AgentUserFactory()
    set_context("agent", user_id=agent.id)

    with rejected(COLUMN_GUARD_ERROR), transaction.atomic():
        User.objects.filter(pk=agent.pk).update(is_superuser=True)


@pytest.mark.parametrize("field", ["role", "organization", "is_active", "email"])
def test_client_cannot_change_protected_columns_of_their_own_row(field):
    client = ClientUserFactory()
    values = {
        "role": "agent",
        "organization": OrganizationFactory(),
        "is_active": False,
        "email": "other@example.com",
    }
    set_context("client", client.organization_id, user_id=client.id)

    with rejected(COLUMN_GUARD_ERROR), transaction.atomic():
        User.objects.filter(pk=client.pk).update(**{field: values[field]})


def test_user_cannot_update_other_users():
    client = ClientUserFactory()
    colleague = ClientUserFactory(organization=client.organization)
    set_context("client", client.organization_id, user_id=client.id)

    assert User.objects.filter(pk=colleague.pk).update(full_name="Changed") == 0


def test_admin_can_change_any_column_of_any_user():
    agent = AgentUserFactory()
    admin = AdminUserFactory()
    set_context("admin", user_id=admin.id)

    assert User.objects.filter(pk=agent.pk).update(is_superuser=True, is_active=False) == 1


@pytest.mark.parametrize("role", ["client", "agent"])
def test_only_admins_create_users(role):
    organization = OrganizationFactory()
    set_context(role, organization.id)

    with rejected(RLS_ERROR), transaction.atomic():
        ClientUserFactory(organization=organization)


# Organizations


def test_client_sees_only_their_organization():
    own = OrganizationFactory()
    OrganizationFactory()
    set_context("client", own.id)

    assert visible_ids(Organization) == {own.id}


@pytest.mark.parametrize("role", ["client", "agent"])
def test_ticket_numbering_can_lock_and_increment_the_counter(role):
    organization = OrganizationFactory()
    set_context(role, organization.id)

    # The pattern the ticket creation service uses.
    locked = Organization.objects.select_for_update().get(pk=organization.pk)
    updated = Organization.objects.filter(pk=locked.pk).update(
        next_ticket_number=F("next_ticket_number") + 1
    )

    assert updated == 1


@pytest.mark.parametrize("role", ["client", "agent"])
def test_only_admins_rename_organizations(role):
    organization = OrganizationFactory()
    set_context(role, organization.id)

    with rejected(COLUMN_GUARD_ERROR), transaction.atomic():
        Organization.objects.filter(pk=organization.pk).update(name="Renamed")


def test_client_cannot_update_another_organization():
    own = OrganizationFactory()
    other = OrganizationFactory()
    set_context("client", own.id)

    assert (
        Organization.objects.filter(pk=other.pk).update(
            next_ticket_number=F("next_ticket_number") + 1
        )
        == 0
    )


def test_admin_can_rename_organizations():
    organization = OrganizationFactory()
    set_context("admin")

    assert Organization.objects.filter(pk=organization.pk).update(name="Renamed") == 1


@pytest.mark.parametrize("role", ["client", "agent"])
def test_only_admins_create_organizations(role):
    calendar = BusinessCalendarFactory()
    set_context(role)

    with rejected(RLS_ERROR), transaction.atomic():
        OrganizationFactory(business_calendar=calendar)


# Catalogs

CATALOGS = [
    (Category, CategoryFactory),
    (BusinessCalendar, BusinessCalendarFactory),
    (BusinessHours, BusinessHoursFactory),
    (Holiday, HolidayFactory),
    (SlaPolicy, SlaPolicyFactory),
]


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
@pytest.mark.parametrize("role", ["client", "agent", "admin", "system"])
def test_every_role_reads_the_catalogs(model, factory, role):
    row = factory()
    set_context(role, OrganizationFactory().id if role == "client" else None)

    assert row.id in visible_ids(model)


@pytest.mark.parametrize(("model", "factory"), CATALOGS)
@pytest.mark.parametrize("role", [None, "login"])
def test_catalogs_are_hidden_without_a_known_role(model, factory, role):
    factory()
    set_context(role)

    assert model.objects.count() == 0


@pytest.mark.parametrize("factory", [CategoryFactory, BusinessCalendarFactory])
@pytest.mark.parametrize("role", ["client", "agent"])
def test_only_admins_create_catalog_rows(factory, role):
    set_context(role, OrganizationFactory().id if role == "client" else None)

    with rejected(RLS_ERROR), transaction.atomic():
        factory()


@pytest.mark.parametrize("role", ["client", "agent"])
def test_only_admins_update_or_delete_catalog_rows(role):
    category = CategoryFactory()
    set_context(role, OrganizationFactory().id if role == "client" else None)

    # Visible, but no policy allows writing: zero rows affected.
    assert Category.objects.filter(pk=category.pk).update(name="Renamed") == 0
    assert Category.objects.filter(pk=category.pk).delete()[0] == 0


def test_admin_writes_catalogs():
    set_context("admin")

    category = CategoryFactory()
    assert Category.objects.filter(pk=category.pk).update(is_active=False) == 1
    assert Category.objects.filter(pk=category.pk).delete()[0] == 1


def test_client_sees_default_and_own_sla_policies_only():
    own = OrganizationFactory()
    default = SlaPolicyFactory(priority=Priority.HIGH)
    own_policy = SlaPolicyFactory(organization=own, priority=Priority.HIGH)
    SlaPolicyFactory(organization=OrganizationFactory(), priority=Priority.HIGH)
    set_context("client", own.id)

    assert visible_ids(SlaPolicy) == {default.id, own_policy.id}
