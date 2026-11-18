import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def test_database_is_postgresql_17():
    # pg_version is an integer: 170005 means 17.5.
    assert connection.vendor == "postgresql"
    assert connection.pg_version // 10000 == 17


def test_suite_runs_as_owner_without_superpowers():
    # A superuser, or a role with BYPASSRLS, skips row-level security: every
    # isolation test would pass without proving anything.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        assert cursor.fetchone() == ("helpdesk_owner", False, False)
