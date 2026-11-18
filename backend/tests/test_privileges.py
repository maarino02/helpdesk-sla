"""Table privileges of the application role, checked against the catalog.

The expected matrix is the documentation: a new table fails
test_app_role_privileges_match_the_matrix until someone decides what the
application may do with it and grants exactly that in a migration.
"""

import pytest
from django.db import ProgrammingError, connection, transaction

pytestmark = pytest.mark.django_db

ALL = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
NO_DELETE = frozenset({"SELECT", "INSERT", "UPDATE"})
APPEND_ONLY = frozenset({"SELECT", "INSERT"})
READ_ONLY = frozenset({"SELECT"})

EXPECTED = {
    # Closed or deactivated, never deleted.
    "ticket": NO_DELETE,
    "organization": NO_DELETE,
    "app_user": NO_DELETE,
    # History.
    "ticket_comment": APPEND_ONLY,
    "ticket_event": APPEND_ONLY,
    "sla_alert": APPEND_ONLY,
    "django_admin_log": APPEND_ONLY,
    # Catalogs (writes limited to admins by row-level security).
    "category": ALL,
    "business_calendar": ALL,
    "business_hours": ALL,
    "holiday": ALL,
    "sla_policy": ALL,
    # Django: sessions, and the group and permission tables of the user admin.
    "django_session": ALL,
    "auth_group": ALL,
    "auth_group_permissions": ALL,
    "app_user_groups": ALL,
    "app_user_user_permissions": ALL,
    # Written only by migrate.
    "django_content_type": READ_ONLY,
    "auth_permission": READ_ONLY,
    "django_migrations": READ_ONLY,
}

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def test_app_role_privileges_match_the_matrix():
    # has_table_privilege answers for any role, whoever runs the query, and
    # includes privileges inherited from PUBLIC.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, p.privilege
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN unnest(%s::text[]) AS p(privilege)
                ON has_table_privilege('helpdesk_app', c.oid, p.privilege)
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            """,
            [list(PRIVILEGES)],
        )
        actual = {}
        for table, privilege in cursor.fetchall():
            privileges = actual.setdefault(table, set())
            if privilege is not None:
                privileges.add(privilege)

    assert actual == EXPECTED


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ticket_event SET from_value = 'x' WHERE false",
        "DELETE FROM ticket_event WHERE false",
        "UPDATE ticket_comment SET body = 'x' WHERE false",
        "DELETE FROM ticket WHERE false",
        "DELETE FROM organization WHERE false",
        "TRUNCATE ticket CASCADE",
        "INSERT INTO django_migrations (app, name, applied) VALUES ('x', 'x', now())",
        "ALTER TABLE ticket DISABLE ROW LEVEL SECURITY",
    ],
)
def test_app_role_cannot(statement):
    # Privileges are checked before any row is read, so WHERE false is enough:
    # no data needed. The savepoint keeps the test transaction usable.
    with pytest.raises(ProgrammingError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement)
