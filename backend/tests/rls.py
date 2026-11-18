"""Helpers for tests that switch the row-level security context."""

from django.db import connection


def set_context(role=None, org_id=None, user_id=None, login_email=None):
    """Replace the whole context for the rest of the test transaction.

    Missing values are set to '', which is how they read on a reused connection;
    the context functions must treat it as NULL.
    """
    values = {
        "app.role": role,
        "app.org_id": org_id,
        "app.user_id": user_id,
        "app.login_email": login_email,
    }
    with connection.cursor() as cursor:
        for name, value in values.items():
            cursor.execute(
                "SELECT set_config(%s, %s, true)", [name, "" if value is None else str(value)]
            )


def fetchone(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()
