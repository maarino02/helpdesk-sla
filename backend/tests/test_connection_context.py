"""The row-level security context must not outlive its transaction.

Connections are reused across requests (CONN_MAX_AGE), so a value left on the
connection by one request would be seen by the next one, possibly another
user's. set_config(..., true) scopes the value to the current transaction.
"""

import pytest
from django.db import connection, transaction

# Real commits: the default test wraps everything in one transaction, which
# would hide exactly what this test is about.
pytestmark = pytest.mark.django_db(transaction=True)


def _current_org_id():
    with connection.cursor() as cursor:
        # missing_ok = true: return NULL instead of failing if never set.
        cursor.execute("SELECT current_setting('app.org_id', true), pg_backend_pid()")
        return cursor.fetchone()


def test_local_setting_does_not_reach_the_next_transaction():
    connection.close()  # Start from a connection that has never seen the setting.

    with transaction.atomic():
        before, pid = _current_org_id()
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.org_id', '42', true)")
        inside, _ = _current_org_id()

    with transaction.atomic():
        after, same_pid = _current_org_id()

    assert same_pid == pid  # Same server session: the connection was reused.
    assert before is None
    assert inside == "42"
    # Not NULL: once a custom setting has been used in a session, PostgreSQL
    # keeps it defined and resets it to an empty string. The row-level security
    # functions must treat '' as missing, e.g. with nullif(..., '').
    assert after == ""
