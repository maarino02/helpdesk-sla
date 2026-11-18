"""Row-level security setup, checked against the PostgreSQL catalog.

Every table in public must be classified: either it has row-level security with
the policies listed here, or it is in WITHOUT_RLS with the reason. A new table
fails test_every_table_is_classified until someone decides which.

Policies are compared by name, command, type and roles, not by expression:
PostgreSQL rewrites the expression text (casts, parentheses), so comparing it
would break on harmless changes. What the policies allow is tested in
test_isolation.py and the other test_rls_* files.
"""

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db

P, R = "PERMISSIVE", "RESTRICTIVE"

CATALOG = {("catalog_read", "SELECT", P), ("catalog_admin", "ALL", P)}

EXPECTED_POLICIES = {
    "ticket": {("tenant_access", "ALL", P)},
    "ticket_comment": {
        ("tenant_access", "ALL", P),
        ("hide_internal_notes_from_clients", "ALL", R),
    },
    "ticket_event": {("tenant_access", "ALL", P)},
    "sla_alert": {("staff_only", "ALL", P)},
    "organization": {
        ("tenant_select", "SELECT", P),
        ("tenant_update", "UPDATE", P),
        ("organization_admin", "INSERT", P),
    },
    "app_user": {
        ("own_row_select", "SELECT", P),
        ("own_row_update", "UPDATE", P),
        ("login_lookup", "SELECT", P),
        ("directory", "SELECT", P),
        ("user_admin", "ALL", P),
    },
    "category": CATALOG,
    "business_calendar": CATALOG,
    "business_hours": CATALOG,
    "holiday": CATALOG,
    "sla_policy": CATALOG,
}

# No client data. Sessions are read before anyone is authenticated; the rest is
# Django bookkeeping written by migrate or used only by the admin.
WITHOUT_RLS = {
    "django_session",
    "django_admin_log",
    "django_content_type",
    "django_migrations",
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "app_user_groups",
    "app_user_user_permissions",
}

EXPECTED_GUARDS = {
    ("app_user", "app_user_restrict_update"): ["password", "last_login", "full_name"],
    ("organization", "organization_restrict_update"): ["next_ticket_number"],
}


def fetchall(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def public_tables():
    return {
        name: (enabled, forced)
        for name, enabled, forced in fetchall(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')
            """
        )
    }


def test_every_table_is_classified():
    classified = set(EXPECTED_POLICIES) | WITHOUT_RLS

    assert not set(EXPECTED_POLICIES) & WITHOUT_RLS
    assert set(public_tables()) == classified


def test_row_level_security_is_enabled_and_forced():
    # FORCE: without it the owner of the table would skip the policies.
    tables = public_tables()

    assert {name for name, flags in tables.items() if flags == (True, True)} == set(
        EXPECTED_POLICIES
    )
    assert {name for name, flags in tables.items() if flags == (False, False)} == WITHOUT_RLS


def test_policies_match():
    actual = {}
    for table, name, command, kind, roles in fetchall(
        """
        SELECT tablename, policyname, cmd, permissive, roles::text[]
        FROM pg_policies
        WHERE schemaname = 'public'
        """
    ):
        # TO public: a policy created for one role would not apply to the others,
        # helpdesk_app included, and that role would see nothing (or, if it were
        # the only permissive policy missing, less than intended).
        assert roles == ["public"], f"{table}.{name} applies to {roles}"
        actual.setdefault(table, set()).add((name, command, kind))

    assert actual == EXPECTED_POLICIES


def test_column_guards_are_installed():
    actual = {}
    for table, name, definition, args in fetchall(
        """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgargs
        FROM pg_trigger AS t
        JOIN pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_proc AS p ON p.oid = t.tgfoid
        WHERE p.proname = 'app_restrict_update' AND t.tgenabled <> 'D'
        """
    ):
        assert "BEFORE UPDATE" in definition and "FOR EACH ROW" in definition
        # tgargs holds the arguments as NUL-terminated strings.
        actual[(table, name)] = bytes(args).decode().split("\x00")[:-1]

    assert actual == EXPECTED_GUARDS


def test_views_check_permissions_of_the_caller():
    # A view runs with its owner's privileges unless security_invoker is set, the
    # classic way to bypass row-level security by accident. Materialized views
    # are snapshots without row-level security, so none may exist unnoticed.
    unsafe = fetchall(
        """
        SELECT relname, relkind
        FROM pg_class
        WHERE relnamespace = 'public'::regnamespace
          AND (relkind = 'm'
               OR (relkind = 'v'
                   AND NOT coalesce('security_invoker=true' = ANY (reloptions), false)))
        """
    )

    assert unsafe == []


def test_application_roles_cannot_bypass_row_level_security():
    assert fetchall(
        """
        SELECT rolname, rolsuper, rolbypassrls
        FROM pg_roles
        WHERE rolname IN ('helpdesk_app', 'helpdesk_owner')
        ORDER BY rolname
        """
    ) == [("helpdesk_app", False, False), ("helpdesk_owner", False, False)]
