"""The auto-close job skips a ticket another transaction holds instead of waiting.

The other transaction keeps FOR KEY SHARE, the weakest lock that conflicts with
FOR UPDATE (inserting a comment takes it through the foreign key). A stronger
lock would also make an UPDATE wait, so the test could pass without skip_locked.
"""

import datetime
import threading

import pytest
from django.db import connection
from django.utils import timezone

from apps.core.rls import system_context
from apps.tickets.models import Status, Ticket
from apps.tickets.tasks import auto_close_resolved
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.usefixtures("production_like")

WAIT = 10  # seconds


def test_locked_ticket_is_skipped_and_closed_by_the_next_run():
    with system_context():
        ticket = TicketFactory(
            resolved=True, resolved_at=timezone.now() - datetime.timedelta(days=8)
        )

    locked, release = threading.Event(), threading.Event()

    def hold_lock():
        try:
            with system_context():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM ticket WHERE id = %s FOR KEY SHARE", [ticket.pk])
                locked.set()
                release.wait(timeout=WAIT)
        finally:
            connection.close()

    outcome = {}

    def run_job():
        try:
            outcome["closed"] = auto_close_resolved()
        finally:
            connection.close()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert locked.wait(timeout=WAIT)
        job = threading.Thread(target=run_job)
        job.start()
        # Must finish while the lock is still held: skipping, not waiting.
        job.join(timeout=WAIT / 2)
        assert not job.is_alive(), "the job waited for the row lock"
    finally:
        release.set()
        holder.join(timeout=WAIT)
    job.join(timeout=WAIT)

    assert outcome["closed"] == 0
    with system_context():
        assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED

    assert auto_close_resolved() == 1
    with system_context():
        assert Ticket.objects.get(pk=ticket.pk).status == Status.CLOSED
