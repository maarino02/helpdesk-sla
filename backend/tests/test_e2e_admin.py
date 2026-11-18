"""The Django admin end to end: real login form, session, rendering, commits.

The admin returns TemplateResponse objects, rendered by Django after the view
returns, outside the ATOMIC_REQUESTS transaction. Querysets evaluated by the
templates (select choices, related objects) would run with no context and see
nothing. AdminRlsMiddleware keeps the view and the rendering in one transaction.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import (
    AdminUserFactory,
    AgentUserFactory,
    ClientUserFactory,
)
from apps.core.rls import system_context

pytestmark = pytest.mark.usefixtures("production_like")

PASSWORD = "password"  # The factory default.


def log_in(client, user):
    return client.post(
        reverse("admin:login"),
        {"username": user.email, "password": PASSWORD, "next": reverse("admin:index")},
    )


def test_select_choices_rendered_by_the_template_are_visible(client):
    with system_context():
        admin = AdminUserFactory(is_staff=True, is_superuser=True)
        customer = ClientUserFactory()
    log_in(client, admin)

    response = client.get(reverse("admin:accounts_user_change", args=[customer.pk]))

    # The organization <select> is filled while the template renders.
    assert response.status_code == 200
    assert f'<option value="{customer.organization_id}" selected>' in response.content.decode()


def test_changelist_lists_users_of_every_organization(client):
    with system_context():
        admin = AdminUserFactory(is_staff=True, is_superuser=True)
        customers = ClientUserFactory.create_batch(2)
    log_in(client, admin)

    content = client.get(reverse("admin:accounts_user_changelist")).content.decode()

    assert all(customer.email in content for customer in customers)


def test_login_records_last_login(client):
    with system_context():
        admin = AdminUserFactory(is_staff=True, is_superuser=True)

    response = log_in(client, admin)

    assert response.status_code == 302
    with system_context():
        assert User.objects.get(pk=admin.pk).last_login is not None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AgentUserFactory(is_staff=True, is_active=False),
        ClientUserFactory,  # is_staff is always false for clients (CHECK).
    ],
    ids=["inactive-staff", "client"],
)
def test_only_active_staff_can_log_in(client, factory):
    with system_context():
        user = factory()

    response = log_in(client, user)

    assert response.status_code == 200  # The form again, with an error.
    assert "_auth_user_id" not in client.session
