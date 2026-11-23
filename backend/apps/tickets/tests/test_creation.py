"""create_ticket inside the test transaction. Concurrency: backend/tests/."""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.sla.services import MissingSlaPolicy
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.services.creation import create_ticket
from apps.tickets.tests.factories import CategoryFactory
from tests.rls import set_context

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


@pytest.fixture
def requester():
    return ClientUserFactory()


@pytest.fixture
def default_policy():
    return SlaPolicyFactory(
        priority=Priority.MEDIUM, first_response_minutes=60, resolution_minutes=480
    )


def create(requester, category=None, **overrides):
    # The default category is only built when none is passed: building it always
    # would INSERT into category, which a client context must not be able to do.
    data = {
        "requester": requester,
        "title": "La impresora no imprime",
        "description": "Sale la hoja en blanco.",
        "category": category or CategoryFactory(),
        "priority": Priority.MEDIUM,
        "now": NOW,
    }
    return create_ticket(**(data | overrides))


@pytest.mark.usefixtures("default_policy")
class TestCreateTicket:
    def test_ticket_fields(self, requester):
        ticket = create(requester)

        assert ticket.organization_id == requester.organization_id
        assert ticket.requester == requester
        assert ticket.status == Status.NEW  # db_default, returned by the INSERT.
        assert ticket.created_at == NOW
        assert ticket.assignee is None

    def test_deadlines_are_computed_from_the_creation_instant(self, requester, default_policy):
        ticket = create(requester)

        assert ticket.sla_policy == default_policy
        # Provisional wall-clock calculation.
        assert ticket.first_response_due_at == NOW + timedelta(minutes=60)
        assert ticket.resolution_due_at == NOW + timedelta(minutes=480)

    def test_organization_policy_is_used_when_it_exists(self, requester):
        own = SlaPolicyFactory(
            organization=requester.organization,
            priority=Priority.MEDIUM,
            first_response_minutes=15,
            resolution_minutes=120,
        )

        ticket = create(requester)

        assert ticket.sla_policy == own
        assert ticket.resolution_due_at == NOW + timedelta(minutes=120)

    def test_numbers_are_consecutive_per_organization(self, requester):
        colleague = ClientUserFactory(organization=requester.organization)
        other_client = ClientUserFactory()

        numbers = [create(user).number for user in (requester, colleague, other_client, requester)]

        assert numbers == [1, 2, 1, 3]
        requester.organization.refresh_from_db()
        assert requester.organization.next_ticket_number == 4

    def test_created_event(self, requester):
        ticket = create(requester)

        event = TicketEvent.objects.get(ticket=ticket)
        assert event.event_type == EventType.CREATED
        assert event.actor == requester
        assert event.organization_id == ticket.organization_id
        assert (event.from_value, event.to_value, event.metadata) == (None, None, {})

    def test_works_in_the_client_row_level_security_context(self, requester):
        # Every write as helpdesk_app with the client's context: the FOR UPDATE and
        # the counter UPDATE on organization (column trigger), and both INSERTs.
        category = CategoryFactory()
        set_context(role="client", org_id=requester.organization_id, user_id=requester.pk)

        ticket = create(requester, category=category)

        assert Ticket.objects.filter(pk=ticket.pk).exists()
        assert TicketEvent.objects.filter(ticket=ticket).exists()


@pytest.mark.usefixtures("default_policy")
class TestValidation:
    def test_agents_cannot_create_tickets(self):
        with pytest.raises(ValidationError) as exc:
            create(AgentUserFactory())

        assert "requester" in exc.value.message_dict

    def test_inactive_requester(self):
        with pytest.raises(ValidationError) as exc:
            create(ClientUserFactory(is_active=False))

        assert "requester" in exc.value.message_dict

    def test_inactive_category(self, requester):
        with pytest.raises(ValidationError) as exc:
            create(requester, category=CategoryFactory(is_active=False))

        assert "category" in exc.value.message_dict

    def test_invalid_priority(self, requester):
        with pytest.raises(ValidationError) as exc:
            create(requester, priority="urgent")

        assert "priority" in exc.value.message_dict

    def test_inactive_organization(self, requester):
        Organization.objects.filter(pk=requester.organization_id).update(is_active=False)

        with pytest.raises(ValidationError):
            create(requester)

    @pytest.mark.parametrize("field", ["requester", "category", "priority"])
    def test_nothing_is_written_on_validation_errors(self, requester, field):
        if field == "requester":
            requester = AgentUserFactory()
        overrides = {
            "requester": {},
            "category": {"category": CategoryFactory(is_active=False)},
            "priority": {"priority": "urgent"},
        }[field]

        with pytest.raises(ValidationError):
            create(requester, **overrides)

        assert not Ticket.objects.exists()
        assert not TicketEvent.objects.exists()


def test_missing_policy_consumes_no_number(requester):
    with pytest.raises(MissingSlaPolicy):
        create(requester, priority=Priority.CRITICAL)

    requester.organization.refresh_from_db()
    assert requester.organization.next_ticket_number == 1
    assert not Ticket.objects.exists()
