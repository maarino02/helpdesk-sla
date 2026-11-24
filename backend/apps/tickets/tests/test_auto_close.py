import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.tickets.models import EventType, Status, Ticket
from apps.tickets.services.auto_close import close_if_stale, stale_resolved_batch
from apps.tickets.tasks import auto_close_resolved
from apps.tickets.tests.factories import TicketFactory
from config.celery import app

pytestmark = pytest.mark.django_db

CUTOFF = datetime.datetime(2026, 11, 17, 10, 0, tzinfo=datetime.UTC)


def resolved(minutes_from_cutoff, **kwargs):
    return TicketFactory(
        resolved=True,
        resolved_at=CUTOFF + datetime.timedelta(minutes=minutes_from_cutoff),
        **kwargs,
    )


def ids(batch):
    return [ticket_id for _resolved_at, ticket_id in batch]


# Candidates


def test_batch_has_the_tickets_resolved_at_or_before_the_cutoff():
    old = resolved(-60)
    at_cutoff = resolved(0)
    resolved(1)  # still inside the client's window
    TicketFactory(status=Status.OPEN)
    TicketFactory(closed=True, resolved_at=CUTOFF - datetime.timedelta(days=30))

    assert ids(stale_resolved_batch(CUTOFF)) == [old.pk, at_cutoff.pk]


def test_batches_page_through_every_candidate_once_in_resolved_order():
    # Ties on resolved_at: the id decides, or a page boundary could skip or repeat.
    tickets = [resolved(-10), resolved(-30), resolved(-30), resolved(-30), resolved(-20)]
    expected = [t.pk for t in sorted(tickets, key=lambda t: (t.resolved_at, t.pk))]

    seen, after = [], None
    for _ in range(len(tickets) + 1):  # bounded: a wrong cursor must fail, not hang
        batch = stale_resolved_batch(CUTOFF, after=after, limit=2)
        if not batch:
            break
        seen += ids(batch)
        after = batch[-1]

    assert seen == expected


def test_later_batches_use_the_partial_index_range_from_the_cursor():
    resolved(-30)
    with CaptureQueriesContext(connection) as queries:
        stale_resolved_batch(CUTOFF, after=(CUTOFF - datetime.timedelta(minutes=30), 1))
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("EXPLAIN (COSTS OFF) " + queries[0]["sql"])
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "Index Scan using ticket_resolved_at_idx" in plan, plan
    # Both ends of the range are index conditions, not a filter after a full scan.
    [index_cond] = [line for line in plan.splitlines() if "Index Cond:" in line]
    assert "resolved_at >= " in index_cond, plan
    assert "resolved_at <= " in index_cond, plan
    assert "Sort" not in plan, plan


# Closing one ticket


def test_close_if_stale_closes_through_the_state_machine():
    ticket = resolved(-60)

    assert close_if_stale(ticket.pk, CUTOFF) is True

    ticket.refresh_from_db()
    assert ticket.status == Status.CLOSED
    assert ticket.closed_at is not None
    assert ticket.sla_paused_at is None
    event = ticket.events.get(event_type=EventType.STATUS_CHANGED)
    assert (event.actor, event.from_value, event.to_value) == (None, "resolved", "closed")


def test_ticket_reopened_after_being_read_is_left_alone():
    ticket = resolved(-60)
    [(_, candidate)] = stale_resolved_batch(CUTOFF)
    Ticket.objects.filter(pk=ticket.pk).update(
        status=Status.OPEN, resolved_at=None, resolution_business_minutes=None, sla_paused_at=None
    )

    assert close_if_stale(candidate, CUTOFF) is False
    assert Ticket.objects.get(pk=ticket.pk).status == Status.OPEN


def test_ticket_closed_by_the_client_after_being_read_is_left_alone():
    # A closed ticket keeps resolved_at: only the status check stops a
    # closed -> closed transition, which would raise and abort the whole run.
    ticket = resolved(-60)
    [(_, candidate)] = stale_resolved_batch(CUTOFF)
    Ticket.objects.filter(pk=ticket.pk).update(
        status=Status.CLOSED, closed_at=CUTOFF, sla_paused_at=None
    )

    assert close_if_stale(candidate, CUTOFF) is False


def test_ticket_resolved_again_after_being_read_is_left_alone():
    # The status is resolved again, so transition() alone would allow the close:
    # only the resolved_at check on the locked row stops it.
    ticket = resolved(-60)
    [(_, candidate)] = stale_resolved_batch(CUTOFF)
    Ticket.objects.filter(pk=ticket.pk).update(resolved_at=CUTOFF + datetime.timedelta(days=6))

    assert close_if_stale(candidate, CUTOFF) is False
    assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED


# The task


def days_ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def test_task_closes_every_stale_ticket_across_batches():
    stale = [TicketFactory(resolved=True, resolved_at=days_ago(8)) for _ in range(5)]
    recent = TicketFactory(resolved=True, resolved_at=days_ago(6))

    assert auto_close_resolved(batch_size=2) == 5

    statuses = dict(Ticket.objects.values_list("pk", "status"))
    assert {statuses[t.pk] for t in stale} == {Status.CLOSED}
    assert statuses[recent.pk] == Status.RESOLVED
    assert auto_close_resolved(batch_size=2) == 0


def test_task_ends_when_the_last_batch_is_exactly_full():
    for _ in range(4):
        TicketFactory(resolved=True, resolved_at=days_ago(8))

    assert auto_close_resolved(batch_size=2) == 4


def test_task_waits_auto_close_after(settings):
    settings.AUTO_CLOSE_AFTER = datetime.timedelta(days=1)
    ticket = TicketFactory(resolved=True, resolved_at=days_ago(2))

    assert auto_close_resolved() == 1
    assert Ticket.objects.get(pk=ticket.pk).status == Status.CLOSED


def test_auto_close_runs_hourly_and_drops_stale_runs():
    entry = app.conf.beat_schedule["auto-close-resolved"]

    assert entry["task"] == auto_close_resolved.name
    assert entry["schedule"] == 3600.0
    assert entry["options"]["expires"] == 3600
