"""Two transitions racing for the same ticket: the case described in ADR-0003.

The auto-close job closes a resolved ticket while the client reopens it. The
client's transition waits for the ticket row lock and is then evaluated against
the status the job left: closed -> open does not exist, so it is rejected.
"""

import threading
import time

import pytest
from django.db import connection, transaction

from apps.accounts.tests.factories import ClientUserFactory
from apps.core.rls import activate_user, system_context
from apps.tickets.models import Status, Ticket
from apps.tickets.services.state_machine import TransitionNotDefined, transition
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.usefixtures("production_like")

WAIT = 10  # seconds


def waiting_lock_requests():
    # pg_locks is readable by every role, unlike the details of other sessions in
    # pg_stat_activity. A lock request not yet granted is a session waiting.
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
        return cursor.fetchone()[0]


def test_reopen_waits_for_the_auto_close_and_is_then_rejected():
    with system_context():
        ticket = TicketFactory(resolved=True)
        client = ClientUserFactory(organization=ticket.organization)

    closed, release = threading.Event(), threading.Event()
    outcome = {}

    def auto_close():
        # Closes the ticket and keeps the transaction (and the lock) open.
        try:
            with system_context():
                transition(ticket, Status.CLOSED, None)
                closed.set()
                release.wait(timeout=WAIT)
        finally:
            connection.close()

    def reopen():
        try:
            with transaction.atomic():
                activate_user(client.pk)
                transition(ticket, Status.OPEN, client)
            outcome["result"] = "reopened"
        except Exception as exc:
            outcome["result"] = exc
        finally:
            connection.close()

    closer = threading.Thread(target=auto_close)
    closer.start()
    try:
        assert closed.wait(timeout=WAIT)
        reopener = threading.Thread(target=reopen)
        reopener.start()

        # The reopen must be blocked on the lock, not finished or failed already.
        deadline = time.monotonic() + WAIT
        while waiting_lock_requests() == 0:
            assert time.monotonic() < deadline, "the reopen never waited for the lock"
            assert "result" not in outcome, f"finished without waiting: {outcome}"
            time.sleep(0.05)
    finally:
        release.set()
        closer.join(timeout=WAIT)
    reopener.join(timeout=WAIT)

    assert isinstance(outcome["result"], TransitionNotDefined)
    with system_context():
        assert Ticket.objects.get(pk=ticket.pk).status == Status.CLOSED
