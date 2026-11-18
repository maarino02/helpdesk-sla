"""Fixtures for the end-to-end tests in this folder."""

import pytest
from django.db import connection
from django.db.backends.signals import connection_created


def _set_app_role(sender=None, connection=connection, **kwargs):
    with connection.cursor() as cursor:
        cursor.execute("SET ROLE helpdesk_app")


@pytest.fixture
def production_like(transactional_db):
    """No test transaction around the requests, connected as helpdesk_app.

    The default tests run inside one transaction with the system context
    (backend/conftest.py), which hides what happens after a request transaction
    commits. Here ATOMIC_REQUESTS, commits and template rendering behave as in
    production, and the session runs as the application role. Create data with
    system_context().

    SET ROLE at session level, also on any connection opened during the test
    (Django may reconnect between requests). RESET before pytest-django
    flushes the database as the owner.
    """
    _set_app_role()
    connection_created.connect(_set_app_role)
    yield
    connection_created.disconnect(_set_app_role)
    with connection.cursor() as cursor:
        cursor.execute("RESET ROLE")
