"""Fixtures shared by the whole test suite."""

import pytest
from django.db import connection


@pytest.fixture(autouse=True)
def _run_as_app_role(request):
    """Run each database test as helpdesk_app, with the system context.

    The test database is created and migrated as helpdesk_owner, which owns
    every table and so has every privilege. Switching role makes the whole
    suite exercise the real grants: a missing GRANT fails a test here instead of
    a request in production.

    app.role = 'system' lets tests create and read rows of every organization.
    Row-level security is forced, so the owner needs it too. Tests about
    isolation set their own context with set_config(..., true).

    SET LOCAL and set_config(..., true) last until the end of the transaction
    pytest-django wraps each test in, and its rollback undoes both. Tests with
    transaction=True have no such transaction, so they keep the owner without
    context and set what they need themselves. Mark a test with
    @pytest.mark.db_owner to keep the owner (still with the system context).
    """
    marker = request.node.get_closest_marker("django_db")
    if marker is None or marker.kwargs.get("transaction"):
        return
    # Make sure the test transaction is open before changing anything inside it.
    request.getfixturevalue("db")
    with connection.cursor() as cursor:
        if not request.node.get_closest_marker("db_owner"):
            cursor.execute("SET LOCAL ROLE helpdesk_app")
        cursor.execute("SELECT set_config('app.role', 'system', true)")
