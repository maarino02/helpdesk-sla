import pytest
from django.db import connection

from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_is_stored_in_app_user_table():
    user = UserFactory()

    # Query the table by name to check the real schema, not just the model's Meta.
    with connection.cursor() as cursor:
        cursor.execute("SELECT username FROM app_user WHERE id = %s", [user.pk])
        assert cursor.fetchone() == (user.username,)
    assert user.check_password("password")
