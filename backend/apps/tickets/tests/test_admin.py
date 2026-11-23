import pytest
from django.urls import reverse

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory
from apps.tickets.models import Category
from apps.tickets.tests.factories import CategoryFactory, SlaAlertFactory, TicketFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    client.force_login(AdminUserFactory(is_staff=True, is_superuser=True))
    return client


@pytest.mark.parametrize("model", ["category", "ticket", "slaalert"])
def test_changelist_renders(admin_client, model):
    SlaAlertFactory()

    response = admin_client.get(reverse(f"admin:tickets_{model}_changelist"))

    assert response.status_code == 200


def test_admin_creates_category(admin_client):
    response = admin_client.post(
        reverse("admin:tickets_category_add"), {"name": "Hardware", "is_active": "on"}
    )

    assert response.status_code == 302
    assert Category.objects.filter(name="Hardware").exists()


def test_admin_shows_form_error_for_duplicate_category(admin_client):
    CategoryFactory(name="Hardware")

    response = admin_client.post(
        reverse("admin:tickets_category_add"), {"name": "hardware", "is_active": "on"}
    )

    assert response.status_code == 200
    assert "Ya existe una categoría con ese nombre." in response.content.decode()


def test_agent_can_view_but_not_change_categories(client):
    # Superuser: Django's permissions allow everything, only the role check denies.
    client.force_login(AgentUserFactory(is_staff=True, is_superuser=True))
    category = CategoryFactory()

    detail = client.get(reverse("admin:tickets_category_change", args=[category.pk]))
    add = client.get(reverse("admin:tickets_category_add"))

    assert detail.status_code == 200
    assert 'name="_save"' not in detail.content.decode()
    assert add.status_code == 403


def test_ticket_detail_is_read_only(admin_client):
    ticket = TicketFactory()

    response = admin_client.get(reverse("admin:tickets_ticket_change", args=[ticket.pk]))

    assert response.status_code == 200
    # Without change permission the admin renders a view page with no save button.
    assert 'name="_save"' not in response.content.decode()


@pytest.mark.parametrize("model", ["ticket", "slaalert"])
def test_rows_written_by_services_cannot_be_added_from_the_admin(admin_client, model):
    response = admin_client.get(reverse(f"admin:tickets_{model}_add"))

    assert response.status_code == 403
