"""Ticket creation racing a calendar change: real connections, client context.

create_ticket reads the calendar before locking the organization. Without
coordination, a ticket created while an admin changes the calendar would keep a
deadline computed with the old schedule, and the recalculation would miss it
because the ticket did not exist yet. ADR-0005: creation holds the calendar's
shared advisory lock, the change holds the exclusive one, and creation checks the
organization's calendar again under the organization lock.

Creation runs as a client on purpose: a row lock (FOR SHARE) on the calendar
would pass for an admin and silently lock nothing for a client, because row locks
must pass the UPDATE policies of row-level security.
"""

import threading
import time

import psycopg.errors
import pytest
from django.db import OperationalError, connection, transaction

from apps.accounts.tests.factories import ClientUserFactory
from apps.core.rls import activate_user, system_context
from apps.organizations.models import Organization
from apps.sla.business_time import add_business_minutes
from apps.sla.models import Priority
from apps.sla.services import load_calendar_schedule, lock_and_snapshot
from apps.sla.tests.factories import BusinessCalendarFactory, BusinessHoursFactory, SlaPolicyFactory
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.sla_recalculation import recalculate_deadlines, tickets_of_organization
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.usefixtures("production_like")

WAIT = 10  # seconds


@pytest.fixture
def setup():
    with system_context():
        SlaPolicyFactory(priority=Priority.MEDIUM)
        customer = ClientUserFactory()  # Its organization has a 24/7 calendar.
        category = CategoryFactory()
        office = BusinessCalendarFactory(timezone="Europe/Madrid")
        for weekday in range(5):
            BusinessHoursFactory(calendar=office, weekday=weekday)
    return customer, category, office


def create(customer, category, lock_timeout=None):
    with transaction.atomic():
        activate_user(customer.pk)
        if lock_timeout:
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
        return create_ticket(
            requester=customer,
            title="Durante el cambio",
            description="Se crea mientras cambia el calendario.",
            category=category,
            priority=Priority.MEDIUM,
        )


def waiting_lock_requests():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
        return cursor.fetchone()[0]


def test_creation_waits_while_the_calendar_is_being_changed(setup):
    customer, category, _ = setup
    with system_context():
        calendar_id = Organization.objects.get(pk=customer.organization_id).business_calendar_id
    locked, release = threading.Event(), threading.Event()

    def change_calendar():
        try:
            with system_context():
                lock_and_snapshot(calendar_id)
                locked.set()
                release.wait(timeout=WAIT)
        finally:
            connection.close()

    admin = threading.Thread(target=change_calendar)
    admin.start()
    try:
        assert locked.wait(timeout=WAIT)
        with pytest.raises(OperationalError) as exc:
            create(customer, category, lock_timeout="200ms")
        assert isinstance(exc.value.__cause__, psycopg.errors.LockNotAvailable)
    finally:
        release.set()
        admin.join(timeout=WAIT)


def test_ticket_created_during_a_switch_gets_the_new_calendar(setup):
    customer, category, office = setup
    organization_id = customer.organization_id
    switched, release = threading.Event(), threading.Event()
    outcome = {}

    def switch_calendar():
        # What OrganizationAdmin does, kept open until the creation is waiting.
        try:
            with system_context():
                organization = Organization.objects.get(pk=organization_id)
                old_id, new_id = organization.business_calendar_id, office.pk
                snapshots = {cid: lock_and_snapshot(cid) for cid in sorted({old_id, new_id})}
                Organization.objects.filter(pk=organization_id).update(business_calendar=office)
                recalculate_deadlines(
                    tickets_of_organization(organization_id), snapshots[old_id], snapshots[new_id]
                )
                switched.set()
                release.wait(timeout=WAIT)
        finally:
            connection.close()

    def create_ticket_meanwhile():
        try:
            outcome["ticket"] = create(customer, category)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            connection.close()

    admin = threading.Thread(target=switch_calendar)
    admin.start()
    try:
        assert switched.wait(timeout=WAIT)
        client = threading.Thread(target=create_ticket_meanwhile)
        client.start()
        # The creation read the old calendar and is waiting for its lock.
        deadline = time.monotonic() + WAIT
        while waiting_lock_requests() == 0:
            assert time.monotonic() < deadline, "the creation never waited"
            time.sleep(0.05)
    finally:
        release.set()
        admin.join(timeout=WAIT)
    client.join(timeout=WAIT)

    assert "error" not in outcome, outcome
    ticket = outcome["ticket"]
    with system_context():
        office_schedule = load_calendar_schedule(office.pk)
    expected = add_business_minutes(
        ticket.created_at, ticket.resolution_target_minutes, office_schedule
    )
    assert ticket.resolution_due_at == expected
