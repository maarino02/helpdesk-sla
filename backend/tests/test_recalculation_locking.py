"""Row locks taken by the deadline recalculation: real connections and commits.

It must lock the tickets it rewrites (a transition in progress makes it wait),
and only them: the filter by calendar joins organization, whose rows ticket
creation locks to reserve numbers.
"""

import threading
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg.errors
import pytest
from django.db import OperationalError, connection
from django.utils import timezone

from apps.core.rls import system_context
from apps.sla.services import load_schedule
from apps.sla.tests.factories import HolidayFactory
from apps.tickets.models import Ticket
from apps.tickets.services.sla_recalculation import recalculate_deadlines, tickets_using_calendar
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.usefixtures("production_like")

WAIT = 10  # seconds


@pytest.fixture
def ticket():
    with system_context():
        return TicketFactory()


def hold(sql, params, locked, release):
    """Run in another thread: lock with `sql` until `release` is set."""
    try:
        with system_context(), connection.cursor() as cursor:
            cursor.execute(sql, params)
            locked.set()
            release.wait(timeout=WAIT)
    finally:
        connection.close()


def recalculate_giving_up_after_200ms(ticket):
    with system_context():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '200ms'")
        calendar = ticket.organization.business_calendar
        old = load_schedule(calendar)
        # Tomorrow, so that it affects the deadline (due in 24 h on a 24/7 calendar).
        tomorrow = timezone.now().astimezone(ZoneInfo(calendar.timezone)).date() + timedelta(1)
        HolidayFactory(calendar=calendar, date=tomorrow)
        return recalculate_deadlines(
            tickets_using_calendar(calendar.pk), old, load_schedule(calendar)
        )


def run_while_holding(sql, params, action):
    locked, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=hold, args=(sql, params, locked, release))
    holder.start()
    try:
        assert locked.wait(timeout=WAIT)
        return action()
    finally:
        release.set()
        holder.join(timeout=WAIT)


def test_locks_the_tickets_it_rewrites(ticket):
    # FOR KEY SHARE, not FOR UPDATE: it conflicts only with SELECT ... FOR UPDATE,
    # not with the UPDATE of the deadlines (non-key columns) nor with the event's
    # foreign key check. Holding FOR UPDATE, the UPDATE would wait too and the test
    # would pass without the explicit lock (the same trap as in ticket numbering).
    with pytest.raises(OperationalError) as exc:
        run_while_holding(
            "SELECT 1 FROM ticket WHERE id = %s FOR KEY SHARE",
            [ticket.pk],
            lambda: recalculate_giving_up_after_200ms(ticket),
        )

    assert isinstance(exc.value.__cause__, psycopg.errors.LockNotAvailable)


def test_does_not_lock_the_organization(ticket):
    # As ticket creation does while it reserves a number.
    moved = run_while_holding(
        "SELECT 1 FROM organization WHERE id = %s FOR UPDATE",
        [ticket.organization_id],
        lambda: recalculate_giving_up_after_200ms(ticket),
    )

    # The holiday tomorrow moves the deadline: the recalculation did not wait.
    assert moved == 1
    with system_context():
        assert Ticket.objects.get(pk=ticket.pk).resolution_due_at > ticket.resolution_due_at
