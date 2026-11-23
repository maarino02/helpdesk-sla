"""Per-client ticket numbering under concurrency: real connections and commits.

Each thread gets its own database connection (Django keeps one per thread), set
to helpdesk_app by the production_like fixture. Threads close their connection
when they finish, so the database can be flushed after the test.
"""

import threading

import psycopg.errors
import pytest
from django.db import OperationalError, connection, transaction

from apps.accounts.tests.factories import ClientUserFactory
from apps.core.rls import activate_user, system_context
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import Ticket
from apps.tickets.services.creation import create_ticket
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.usefixtures("production_like")

THREADS = 5
WAIT = 10  # seconds; a deadlock or a missed signal fails the test instead of hanging it


@pytest.fixture
def setup():
    with system_context():
        SlaPolicyFactory(priority=Priority.MEDIUM)
        requester = ClientUserFactory()
        category = CategoryFactory()
    return requester, category


def create_as(requester, category):
    """What a request does: open a transaction, set the user's context, create."""
    with transaction.atomic():
        activate_user(requester.pk)
        return create_ticket(
            requester=requester,
            title="Concurrent",
            description="Created at the same time as others.",
            category=category,
            priority=Priority.MEDIUM,
        )


def test_concurrent_creations_get_consecutive_numbers(setup):
    requester, category = setup
    barrier = threading.Barrier(THREADS)
    numbers, errors = [], []

    def worker():
        try:
            barrier.wait(timeout=WAIT)  # All threads reach create_ticket together.
            numbers.append(create_as(requester, category).number)
        except Exception as exc:  # Reported by the main thread.
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT)

    assert errors == []
    assert sorted(numbers) == list(range(1, THREADS + 1))
    with system_context():
        assert Ticket.objects.count() == THREADS
        organization = Organization.objects.get(pk=requester.organization_id)
        assert organization.next_ticket_number == THREADS + 1
        # The creation instant is taken after the lock, so creation times follow
        # the numbers: a creation that waited is never older than the one before.
        by_number = list(Ticket.objects.order_by("number").values_list("created_at", flat=True))
    assert by_number == sorted(by_number)


def test_creation_waits_for_the_organization_row_lock(setup):
    requester, category = setup
    locked, release = threading.Event(), threading.Event()

    def hold_lock():
        # FOR KEY SHARE, the weakest row lock, the one foreign key checks take. It
        # conflicts with SELECT ... FOR UPDATE but neither with the counter UPDATE
        # (FOR NO KEY UPDATE: next_ticket_number is not a key column) nor with the
        # ticket INSERT's foreign key check. Holding it, only the explicit
        # FOR UPDATE waits: holding FOR UPDATE instead, the UPDATE would wait too
        # and the test would pass without the explicit lock.
        try:
            with system_context(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM organization WHERE id = %s FOR KEY SHARE",
                    [requester.organization_id],
                )
                locked.set()
                release.wait(timeout=WAIT)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert locked.wait(timeout=WAIT)
        # Instead of waiting for the lock, give up after 200 ms: proves that
        # create_ticket asks for the organization row lock.
        with pytest.raises(OperationalError) as exc, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '200ms'")
            activate_user(requester.pk)
            create_ticket(
                requester=requester,
                title="Blocked",
                description="Waits for the lock.",
                category=category,
                priority=Priority.MEDIUM,
            )
        assert isinstance(exc.value.__cause__, psycopg.errors.LockNotAvailable)
    finally:
        release.set()
        holder.join(timeout=WAIT)
