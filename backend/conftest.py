"""Fixtures shared by the whole test suite."""

import pytest
from django.db import connection


@pytest.fixture(autouse=True)
def _run_as_app_role(request):
    """Run each database test as helpdesk_app, the role the application uses.

    The test database is created and migrated as helpdesk_owner, which owns
    every table and so has every privilege. Switching role makes the whole
    suite exercise the real grants: a missing GRANT fails a test here instead of
    a request in production.

    SET LOCAL lasts until the end of the transaction pytest-django wraps each
    test in, and its rollback restores the owner. Tests with transaction=True
    have no such transaction, so they keep the owner and switch role themselves
    if they need to. Mark a test with @pytest.mark.db_owner to keep the owner.
    """
    marker = request.node.get_closest_marker("django_db")
    if marker is None or marker.kwargs.get("transaction"):
        return
    if request.node.get_closest_marker("db_owner"):
        return
    # Make sure the test transaction is open before switching role inside it.
    request.getfixturevalue("db")
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE helpdesk_app")
