import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.tests.factories import AgentUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.models import Category, Status
from apps.tickets.tests.factories import CategoryFactory, TicketFactory

pytestmark = pytest.mark.django_db


def assert_violates(constraint_name, factory, **kwargs):
    # The database must reject the row; the savepoint keeps the test transaction usable.
    # Each test breaks exactly one rule: with several, PostgreSQL reports only the first
    # failing CHECK (in alphabetical order of name).
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


# Defaults and representation


def test_new_ticket_gets_database_defaults():
    ticket = TicketFactory()

    assert ticket.status == Status.NEW
    assert ticket.created_at is not None
    assert ticket.updated_at is not None


def test_status_defaults_to_new_on_raw_insert():
    ticket = TicketFactory()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO ticket (organization_id, number, title, description, category_id,"
            " priority, requester_id, sla_policy_id, first_response_due_at,"
            " resolution_due_at, updated_at)"
            " SELECT organization_id, number + 1, title, description, category_id,"
            " priority, requester_id, sla_policy_id, first_response_due_at,"
            " resolution_due_at, updated_at FROM ticket WHERE id = %s"
            " RETURNING status",
            [ticket.pk],
        )
        assert cursor.fetchone() == ("new",)


def test_str_is_the_client_reference():
    ticket = TicketFactory(organization=OrganizationFactory(slug="acme"), number=42)

    assert str(ticket) == "ACME-42"


# Numbering


def test_number_is_unique_per_organization():
    existing = TicketFactory()

    assert_violates(
        "ticket_unique_organization_number",
        TicketFactory,
        organization=existing.organization,
        number=existing.number,
    )


def test_same_number_is_allowed_in_another_organization():
    existing = TicketFactory()

    TicketFactory(number=existing.number)


@pytest.mark.parametrize("number", [0, -1])
def test_number_must_be_positive(number):
    assert_violates("ticket_number_positive", TicketFactory, number=number)


# Enumerated values


def test_invalid_priority_is_rejected():
    # Pass a valid policy explicitly: the factory looks it up by priority.
    assert_violates(
        "ticket_priority_valid",
        TicketFactory,
        priority="urgent",
        sla_policy=TicketFactory().sla_policy,
    )


def test_invalid_status_is_rejected():
    assert_violates("ticket_status_valid", TicketFactory, status="archived")


# Status and timestamps


def test_resolved_and_closed_tickets_are_consistent():
    TicketFactory(resolved=True)
    TicketFactory(closed=True)


def test_closed_ticket_requires_closed_at():
    assert_violates("ticket_closed_at_matches_status", TicketFactory, closed=True, closed_at=None)


def test_only_closed_tickets_have_closed_at():
    assert_violates(
        "ticket_closed_at_matches_status",
        TicketFactory,
        resolved=True,
        closed_at=timezone.now(),
    )


def test_resolved_ticket_requires_resolved_at():
    assert_violates(
        "ticket_resolved_at_matches_status",
        TicketFactory,
        status=Status.RESOLVED,
    )


@pytest.mark.parametrize("status", [Status.NEW, Status.OPEN, Status.PENDING])
def test_unresolved_ticket_cannot_have_resolved_at(status):
    # E.g. a reopened ticket (resolved -> open) whose resolved_at was not cleared.
    assert_violates(
        "ticket_resolved_at_matches_status",
        TicketFactory,
        status=status,
        resolved_at=timezone.now(),
        resolution_business_minutes=60,
    )


def test_first_response_minutes_require_the_timestamp():
    assert_violates(
        "ticket_first_response_minutes_with_timestamp",
        TicketFactory,
        first_response_business_minutes=30,
    )


def test_first_response_timestamp_requires_the_minutes():
    assert_violates(
        "ticket_first_response_minutes_with_timestamp",
        TicketFactory,
        first_response_at=timezone.now(),
    )


def test_resolution_timestamp_requires_the_minutes():
    assert_violates(
        "ticket_resolution_minutes_with_timestamp",
        TicketFactory,
        resolved=True,
        resolution_business_minutes=None,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"first_response_at": timezone.now(), "first_response_business_minutes": -1},
        {"resolved": True, "resolution_business_minutes": -1},
    ],
)
def test_business_minutes_cannot_be_negative(fields):
    assert_violates("ticket_business_minutes_non_negative", TicketFactory, **fields)


def test_null_business_minutes_pass_the_non_negative_check():
    # A CHECK that evaluates to NULL passes; also in Django's Python validation.
    ticket = TicketFactory()

    ticket.full_clean()


# Protected references


@pytest.mark.parametrize(
    "related",
    ["organization", "category", "requester", "sla_policy"],
)
def test_referenced_rows_cannot_be_deleted(related):
    ticket = TicketFactory()

    with pytest.raises(ProtectedError):
        getattr(ticket, related).delete()


def test_assignee_cannot_be_deleted():
    ticket = TicketFactory(assignee=AgentUserFactory())

    with pytest.raises(ProtectedError):
        ticket.assignee.delete()


# Category


def test_category_name_is_unique_ignoring_case():
    CategoryFactory(name="Hardware")

    assert_violates("category_unique_name_ci", CategoryFactory, name="hardware")


def test_category_name_keeps_its_case():
    CategoryFactory(name="VPN")

    assert Category.objects.get().name == "VPN"


def test_category_uniqueness_is_also_validated_by_full_clean():
    CategoryFactory(name="Hardware")

    with pytest.raises(ValidationError, match="Ya existe una categoría"):
        Category(name="HARDWARE").full_clean()


def test_category_is_active_by_default():
    assert CategoryFactory().is_active is True
