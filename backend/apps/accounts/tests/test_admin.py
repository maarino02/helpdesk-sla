import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import AdminUserFactory
from apps.organizations.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db

ADD_URL = reverse("admin:accounts_user_add")


@pytest.fixture
def admin_client(client):
    client.force_login(AdminUserFactory(is_staff=True, is_superuser=True))
    return client


def add_user_form(**overrides):
    data = {
        "email": "Ana@Acme.com",
        "full_name": "Ana García",
        "role": Role.AGENT,
        "organization": "",
        "usable_password": "true",
        "password1": "a-Strong-passw0rd",
        "password2": "a-Strong-passw0rd",
    }
    return data | overrides


def test_add_user_page_renders(admin_client):
    response = admin_client.get(ADD_URL)

    assert response.status_code == 200


def test_change_user_page_renders(admin_client):
    user = AdminUserFactory()

    response = admin_client.get(reverse("admin:accounts_user_change", args=[user.pk]))

    assert response.status_code == 200


def test_admin_creates_user_with_normalized_email(admin_client):
    response = admin_client.post(ADD_URL, add_user_form())

    assert response.status_code == 302  # redirect to the change page on success
    assert User.objects.get(email="ana@acme.com").check_password("a-Strong-passw0rd")


def test_admin_creates_client_user_with_organization(admin_client):
    organization = OrganizationFactory()

    response = admin_client.post(
        ADD_URL, add_user_form(role=Role.CLIENT, organization=organization.pk)
    )

    assert response.status_code == 302
    assert User.objects.get(email="ana@acme.com").organization == organization


def test_admin_shows_form_error_for_client_without_organization(admin_client):
    response = admin_client.post(ADD_URL, add_user_form(role=Role.CLIENT))

    assert response.status_code == 200  # form redisplayed, not a 500
    assert "deben pertenecer a una organización" in response.content.decode()
    assert not User.objects.filter(email="ana@acme.com").exists()
