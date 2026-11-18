import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def _fetchone(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()


def test_database_is_postgresql_17():
    # pg_version is an integer: 170005 means 17.5.
    assert connection.vendor == "postgresql"
    assert connection.pg_version // 10000 == 17


def test_suite_connects_as_owner_without_superpowers():
    # A superuser, or a role with BYPASSRLS, skips row-level security: every
    # isolation test would pass without proving anything. session_user is the
    # role that logged in; SET ROLE changes only current_user.
    assert _fetchone(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = session_user"
    ) == ("helpdesk_owner", False, False)


def test_tests_run_as_app_role():
    assert _fetchone("SELECT current_user") == ("helpdesk_app",)


@pytest.mark.db_owner
def test_db_owner_marker_keeps_the_owner():
    assert _fetchone("SELECT current_user") == ("helpdesk_owner",)
