import pytest
from django.db import IntegrityError, connection, transaction

from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.models import AlertKind, AlertLevel
from apps.tickets.tests.factories import SlaAlertFactory, TicketFactory

pytestmark = pytest.mark.django_db

# The statement the SLA job (phase 6) will run. RETURNING only yields a row when the
# INSERT actually happened, so the job notifies only for new alerts.
INSERT_ALERT_ONCE = """
    INSERT INTO sla_alert (ticket_id, organization_id, kind, level, due_at)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (ticket_id, kind, level) DO NOTHING
    RETURNING id
"""


def assert_violates(constraint_name, factory, **kwargs):
    # The database must reject the row; the savepoint keeps the test transaction usable.
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


def insert_alert_once(ticket, kind, level):
    with connection.cursor() as cursor:
        cursor.execute(
            INSERT_ALERT_ONCE,
            [ticket.pk, ticket.organization_id, kind, level, ticket.resolution_due_at],
        )
        return cursor.fetchone()


def test_alert_gets_created_at_from_the_database():
    assert SlaAlertFactory().created_at is not None


def test_alert_keeps_the_deadline_it_was_raised_for():
    alert = SlaAlertFactory()
    original_due_at = alert.due_at

    # E.g. the deadline is extended after a pause: the alert must not change.
    alert.ticket.resolution_due_at = original_due_at.replace(year=original_due_at.year + 1)
    alert.ticket.save()
    alert.refresh_from_db()

    assert alert.due_at == original_due_at


def test_same_alert_cannot_be_raised_twice():
    alert = SlaAlertFactory()

    assert_violates(
        "sla_alert_unique_ticket_kind_level",
        SlaAlertFactory,
        ticket=alert.ticket,
        kind=alert.kind,
        level=alert.level,
    )


def test_each_kind_and_level_is_a_different_alert():
    ticket = TicketFactory()

    for kind in AlertKind.values:
        for level in AlertLevel.values:
            SlaAlertFactory(ticket=ticket, kind=kind, level=level)

    assert ticket.sla_alerts.count() == 4


def test_insert_on_conflict_is_idempotent():
    ticket = TicketFactory()

    first = insert_alert_once(ticket, AlertKind.RESOLUTION, AlertLevel.WARNING)
    second = insert_alert_once(ticket, AlertKind.RESOLUTION, AlertLevel.WARNING)

    assert first is not None  # inserted: the job would notify
    assert second is None  # conflict, nothing inserted: no second notification
    assert ticket.sla_alerts.count() == 1


def test_invalid_kind_is_rejected():
    assert_violates("sla_alert_kind_valid", SlaAlertFactory, kind="update")


def test_invalid_level_is_rejected():
    assert_violates("sla_alert_level_valid", SlaAlertFactory, level="critical")


def test_alert_organization_must_be_the_tickets():
    assert_violates(
        "sla_alert_ticket_same_organization_fk",
        SlaAlertFactory,
        organization=OrganizationFactory(),
    )


def test_composite_fk_replaces_simple_foreign_keys():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, condeferrable, confupdtype, confdeltype FROM pg_constraint "
            "WHERE conrelid = 'sla_alert'::regclass AND contype = 'f'"
        )
        foreign_keys = cursor.fetchall()

    assert foreign_keys == [("sla_alert_ticket_same_organization_fk", False, "a", "a")]


def test_no_single_column_ticket_index():
    # ticket_id is covered by the unique (ticket_id, kind, level) index.
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'sla_alert'")
        definitions = [row[0] for row in cursor.fetchall()]

    assert not any(d.endswith(("(ticket_id)", "(organization_id)")) for d in definitions)
