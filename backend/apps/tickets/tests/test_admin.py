import pytest
from django.urls import reverse

from apps.accounts.tests.factories import AdminUserFactory
from apps.tickets.models import Category
from apps.tickets.tests.factories import CategoryFactory, TicketFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    client.force_login(AdminUserFactory(is_staff=True, is_superuser=True))
    return client


@pytest.mark.parametrize("model", ["category", "ticket"])
def test_changelist_renders(admin_client, model):
    TicketFactory()

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


def test_ticket_detail_is_read_only(admin_client):
    ticket = TicketFactory()

    response = admin_client.get(reverse("admin:tickets_ticket_change", args=[ticket.pk]))

    assert response.status_code == 200
    # Without change permission the admin renders a view page with no save button.
    assert 'name="_save"' not in response.content.decode()


def test_tickets_cannot_be_added_from_the_admin(admin_client):
    response = admin_client.get(reverse("admin:tickets_ticket_add"))

    assert response.status_code == 403
