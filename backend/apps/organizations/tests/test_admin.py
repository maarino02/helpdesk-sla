from datetime import timedelta

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory
from apps.organizations.models import Organization
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.tests.factories import BusinessCalendarFactory, BusinessHoursFactory
from apps.tickets.models import EventType, Ticket, TicketEvent
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    client.force_login(AdminUserFactory(is_staff=True, is_superuser=True))
    return client


@pytest.fixture
def agent_client(client):
    # Superuser, so Django's permissions allow everything: only the role check
    # stands between the agent and the forms.
    client.force_login(AgentUserFactory(is_staff=True, is_superuser=True))
    return client


def test_changelist_renders(admin_client):
    OrganizationFactory()

    response = admin_client.get(reverse("admin:organizations_organization_changelist"))

    assert response.status_code == 200


def test_admin_creates_organization_with_database_defaults(admin_client):
    calendar = BusinessCalendarFactory()

    response = admin_client.post(
        reverse("admin:organizations_organization_add"),
        {"name": "Acme", "slug": "acme", "business_calendar": calendar.pk},
    )

    assert response.status_code == 302
    organization = Organization.objects.get(slug="acme")
    assert organization.next_ticket_number == 1
    assert organization.is_active is True


def test_add_form_shows_the_slug_format_error(admin_client):
    calendar = BusinessCalendarFactory()

    response = admin_client.post(
        reverse("admin:organizations_organization_add"),
        {"name": "Acme", "slug": "-acme", "business_calendar": calendar.pk},
    )

    assert response.status_code == 200  # The form again, not an IntegrityError.
    assert not Organization.objects.exists()


def test_slug_and_ticket_counter_cannot_be_changed(admin_client):
    organization = OrganizationFactory(slug="acme", next_ticket_number=5)

    response = admin_client.post(
        reverse("admin:organizations_organization_change", args=[organization.pk]),
        {
            "name": "Acme renamed",
            "slug": "other",
            "business_calendar": organization.business_calendar_id,
            "is_active": "on",
            "next_ticket_number": 1,
        },
    )

    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.name == "Acme renamed"
    # Read-only fields are not in the form: the posted values are ignored.
    assert organization.slug == "acme"
    assert organization.next_ticket_number == 5


def test_organization_can_be_deactivated(admin_client):
    organization = OrganizationFactory()

    admin_client.post(
        reverse("admin:organizations_organization_change", args=[organization.pk]),
        {"name": organization.name, "business_calendar": organization.business_calendar_id},
    )

    organization.refresh_from_db()
    assert organization.is_active is False  # Unchecked checkbox.


def test_organizations_cannot_be_deleted(admin_client):
    organization = OrganizationFactory()

    response = admin_client.get(
        reverse("admin:organizations_organization_delete", args=[organization.pk])
    )

    assert response.status_code == 403


def test_agent_can_view_but_not_add_or_change(agent_client):
    organization = OrganizationFactory()

    detail = agent_client.get(
        reverse("admin:organizations_organization_change", args=[organization.pk])
    )
    add = agent_client.get(reverse("admin:organizations_organization_add"))

    assert detail.status_code == 200
    assert 'name="_save"' not in detail.content.decode()
    assert add.status_code == 403


def test_switching_calendars_reprojects_the_organization_tickets(admin_client):
    organization = OrganizationFactory()  # 24/7.
    ticket = TicketFactory(organization=organization)
    other = TicketFactory()  # Another organization, also 24/7.
    office = BusinessCalendarFactory(timezone="Europe/Madrid")
    for weekday in range(5):
        BusinessHoursFactory(calendar=office, weekday=weekday)
    old_calendar_id = organization.business_calendar_id

    response = admin_client.post(
        reverse("admin:organizations_organization_change", args=[organization.pk]),
        {"name": organization.name, "business_calendar": office.pk, "is_active": "on"},
        follow=True,
    )

    assert "Plazos de SLA recalculados en 1 incidencia." in response.content.decode()
    # 24 business hours on an office calendar are about three working days.
    updated = Ticket.objects.get(pk=ticket.pk)
    assert updated.resolution_due_at > ticket.resolution_due_at + timedelta(days=1)
    event = TicketEvent.objects.get(ticket=ticket, event_type=EventType.SLA_RECALCULATED)
    assert event.metadata["organization_calendar"] == {"from": old_calendar_id, "to": office.pk}
    assert Ticket.objects.get(pk=other.pk).resolution_due_at == other.resolution_due_at


def test_saving_without_switching_calendars_records_nothing(admin_client):
    organization = OrganizationFactory()
    TicketFactory(organization=organization)

    admin_client.post(
        reverse("admin:organizations_organization_change", args=[organization.pk]),
        {
            "name": "Nuevo nombre",
            "business_calendar": organization.business_calendar_id,
            "is_active": "on",
        },
    )

    assert not TicketEvent.objects.filter(event_type=EventType.SLA_RECALCULATED).exists()
