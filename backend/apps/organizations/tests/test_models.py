import datetime

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.organizations.models import Organization
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.tests.factories import BusinessCalendarFactory

pytestmark = pytest.mark.django_db


def assert_violates(constraint_name, factory, **kwargs):
    # Same pattern as the sla tests: the database must reject the row, and the
    # savepoint keeps the test transaction usable after the error.
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


def test_database_defaults_apply_to_raw_inserts():
    # db_default lives in the table definition, so an INSERT that bypasses the ORM
    # and omits these columns still gets them.
    calendar = BusinessCalendarFactory()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO organization (name, slug, business_calendar_id) "
            "VALUES ('Raw', 'raw', %s) "
            "RETURNING next_ticket_number, is_active, created_at",
            [calendar.pk],
        )
        next_number, is_active, created_at = cursor.fetchone()

    assert next_number == 1
    assert is_active is True
    assert created_at.tzinfo is not None  # timestamptz, returned as an aware datetime


def test_orm_gets_database_defaults_back_on_insert():
    # On PostgreSQL, Django adds db_default columns to INSERT ... RETURNING, so the
    # instance has the real values without refresh_from_db().
    organization = OrganizationFactory()

    assert organization.next_ticket_number == 1
    assert organization.is_active is True
    assert isinstance(organization.created_at, datetime.datetime)


def test_slug_is_unique():
    existing = OrganizationFactory(slug="acme")

    assert_violates("organization_unique_slug", OrganizationFactory, slug=existing.slug)


@pytest.mark.parametrize("slug", ["ACME", "acme_iberia", "-acme", "acme-", "acme--iberia", ""])
def test_invalid_slug_format_is_rejected(slug):
    assert_violates("organization_slug_format", OrganizationFactory, slug=slug)


@pytest.mark.parametrize("slug", ["acme", "acme-iberia", "client-42"])
def test_valid_slug_format_is_accepted(slug):
    OrganizationFactory(slug=slug)


@pytest.mark.parametrize("value", [0, -1])
def test_next_ticket_number_must_be_positive(value):
    assert_violates(
        "organization_next_ticket_number_positive",
        OrganizationFactory,
        next_ticket_number=value,
    )


def test_calendar_in_use_cannot_be_deleted():
    organization = OrganizationFactory()

    with pytest.raises(ProtectedError):
        organization.business_calendar.delete()

    assert Organization.objects.filter(pk=organization.pk).exists()
