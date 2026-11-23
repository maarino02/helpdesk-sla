"""Data migrations, run for real: migrate back, insert old-style rows, migrate forward.

Rows are created with the historical models of the old migration state: the
current models have columns the old schema lacks. transaction=True because
migrations run their own transactions; without the test transaction, the
connection stays the owner with no context (backend/conftest.py), so the inserts
set app.role = 'system' themselves.
"""

from datetime import UTC, datetime, time

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = ("tickets", "0010_category_row_level_security")
AFTER = ("tickets", "0012_ticket_sla_constraints")
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
UPDATED = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
RESOLVED = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)


def migrate(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(list(targets))
    return executor.loader.project_state(list(targets)).apps


def migrate_to_latest():
    # Every app's last migration, not a fixed name: a fixed one would leave the
    # schema behind as soon as a newer migration exists, and the transactional
    # tests that run afterwards would see an old schema.
    executor = MigrationExecutor(connection)
    migrate(*executor.loader.graph.leaf_nodes())


def system_context():
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.role', 'system', true)")


@pytest.fixture
def old_tickets():
    """One ticket per status, created with the schema of 0010. Leaves the schema
    at the latest migration afterwards, whatever the test did.
    """
    old = migrate(BEFORE)
    with transaction.atomic():
        system_context()
        calendar = old.get_model("sla", "BusinessCalendar").objects.create(
            name="Madrid", timezone="Europe/Madrid"
        )
        old.get_model("sla", "BusinessHours").objects.create(
            calendar=calendar, weekday=0, start_time=time(9), end_time=time(18)
        )
        organization = old.get_model("organizations", "Organization").objects.create(
            name="Acme", slug="acme", business_calendar=calendar
        )
        policy = old.get_model("sla", "SlaPolicy").objects.create(
            priority="medium", first_response_minutes=60, resolution_minutes=480
        )
        category = old.get_model("tickets", "Category").objects.create(name="Hardware")
        requester = old.get_model("accounts", "User").objects.create(
            email="client@acme.test", full_name="Client", role="client", organization=organization
        )
        Ticket = old.get_model("tickets", "Ticket")
        tickets = {}
        for number, status in enumerate(["new", "open", "pending", "resolved", "closed"], 1):
            done = status in ("resolved", "closed")
            tickets[status] = Ticket.objects.create(
                organization=organization,
                number=number,
                title=status,
                description="Old ticket.",
                category=category,
                priority="medium",
                status=status,
                requester=requester,
                sla_policy=policy,
                first_response_due_at=T0,
                resolution_due_at=T0,
                resolved_at=RESOLVED if done else None,
                resolution_business_minutes=60 if done else None,
                closed_at=RESOLVED if status == "closed" else None,
            ).pk
        # auto_now would overwrite updated_at on save; an UPDATE sets a known value.
        Ticket.objects.update(updated_at=UPDATED)
    yield tickets
    migrate_to_latest()


def test_fills_the_target_and_the_pause_start(old_tickets):
    migrate(AFTER)

    with transaction.atomic():
        system_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, resolution_target_minutes, sla_paused_at FROM ticket")
            rows = {pk: (target, paused) for pk, target, paused in cursor.fetchall()}

    assert {target for target, _ in rows.values()} == {480}  # From the policy.
    paused = {status: rows[pk][1] for status, pk in old_tickets.items()}
    assert paused == {
        "new": None,
        "open": None,
        "pending": UPDATED,  # Best known instant: the last update.
        "resolved": RESOLVED,
        "closed": None,  # Stopped, not paused.
    }
