"""The replayed history: invariants that must hold on the stored data.

The services are the ones that compute everything; these tests check that the
plan drives them into a consistent database, as the dashboard will read it.
"""

import datetime as dt

import pytest
from django.db import connection
from django.db.models import Max, Min

from apps.accounts.models import Role, User
from apps.demo import reset
from apps.demo.plan import LAST_TICKET_BEFORE_RETIRING, RETIRED_BEFORE_NOW
from apps.demo.seed import seed_demo
from apps.tickets import tasks
from apps.tickets.models import Category, EventType, Status, Ticket, TicketComment, TicketEvent
from apps.tickets.services.sla_alerts import raise_sla_alerts

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def summary(django_db_setup, django_db_blocker):
    """Seeded once for the module (about 10 s) and committed, so each test reads it
    inside its own rolled-back transaction. Emptied at the end, as the owner (the
    test database's connection), for the tests that follow."""
    enqueued = []
    with django_db_blocker.unblock(), pytest.MonkeyPatch.context() as patch:
        # The commit is real: on_commit callbacks would run and enqueue here.
        patch.setattr(tasks.notify, "delay", lambda *args: enqueued.append(args))
        summary = seed_demo(password="x", tickets=80, seed=3, now=NOW)
        summary.enqueued = enqueued
        yield summary
        reset.truncate(connection)


def test_nothing_is_notified(summary):
    # Months of made-up status changes and alerts: nobody must get an email.
    assert summary.enqueued == []


def test_every_ticket_is_created_and_most_are_finished(summary):
    assert summary.tickets_by_status.total() == 80
    assert summary.tickets_by_status[Status.CLOSED] > 60


def test_status_matches_the_last_status_change(summary):
    for ticket in Ticket.objects.prefetch_related("events"):
        changes = [e for e in ticket.events.all() if e.event_type == EventType.STATUS_CHANGED]
        assert ticket.status == (changes[-1].to_value if changes else Status.NEW)


def test_timestamps_match_the_status(summary):
    for ticket in Ticket.objects.all():
        finished = ticket.status in (Status.RESOLVED, Status.CLOSED)
        assert (ticket.resolved_at is not None) == finished
        assert (ticket.resolution_business_minutes is not None) == finished
        assert (ticket.closed_at is not None) == (ticket.status == Status.CLOSED)
        assert (ticket.first_response_at is None) == (ticket.status == Status.NEW)


def test_first_response_is_the_first_public_staff_reply(summary):
    for ticket in Ticket.objects.exclude(status=Status.NEW):
        first = (
            TicketComment.objects.filter(
                ticket=ticket, is_internal=False, author__role__in=[Role.AGENT, Role.ADMIN]
            )
            .order_by("created_at")
            .first()
        )
        assert ticket.first_response_at == first.created_at


def test_history_lies_between_creation_and_now_in_insertion_order(summary):
    for ticket in Ticket.objects.prefetch_related("events", "comments"):
        events = list(ticket.events.all())  # ordered by created_at, id
        assert [e.pk for e in events] == sorted(e.pk for e in events)
        for row in [*events, *ticket.comments.all()]:
            assert ticket.created_at <= row.created_at <= NOW


def test_numbers_follow_creation_order_within_each_client(summary):
    for organization in summary.catalog.organizations:
        numbers = list(
            Ticket.objects.filter(organization=organization)
            .order_by("created_at")
            .values_list("number", flat=True)
        )
        assert numbers == list(range(1, len(numbers) + 1))


def test_updated_at_is_the_last_activity(summary):
    for ticket in Ticket.objects.all():
        last = max(
            TicketEvent.objects.filter(ticket=ticket).aggregate(at=Max("created_at"))["at"],
            TicketComment.objects.filter(ticket=ticket).aggregate(at=Max("created_at"))["at"]
            or ticket.created_at,
        )
        assert ticket.updated_at == last


def test_retired_people_and_categories_are_inactive_and_only_on_old_tickets(summary):
    cutoff = NOW - RETIRED_BEFORE_NOW - LAST_TICKET_BEFORE_RETIRING
    retired_users = User.objects.filter(is_active=False)
    retired_categories = Category.objects.filter(is_active=False)

    assert retired_users.count() == 2 and retired_categories.count() == 1
    newest = Ticket.objects.filter(category__in=retired_categories).aggregate(at=Max("created_at"))
    assert newest["at"] is None or newest["at"] < cutoff
    newest = Ticket.objects.filter(requester__in=retired_users).aggregate(at=Max("created_at"))
    assert newest["at"] is None or newest["at"] < cutoff


def test_the_alert_job_finds_nothing_new_at_the_same_instant(summary):
    assert raise_sla_alerts(NOW) == []


def test_history_covers_the_whole_period(summary):
    span = Ticket.objects.aggregate(first=Min("created_at"), last=Max("created_at"))

    assert span["first"] < NOW - dt.timedelta(days=150)
    assert span["last"] > NOW - dt.timedelta(days=14)
