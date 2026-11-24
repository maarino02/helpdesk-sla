import datetime
import re

import pytest
from django.db import connection

from apps.tickets.models import AlertKind, AlertLevel, SlaAlert, Status
from apps.tickets.services import sla_alerts
from apps.tickets.services.sla_alerts import RaisedAlert, raise_sla_alerts
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 11, 24, 10, 0, tzinfo=datetime.UTC)
FAR = (60 * 24 * 30, 60 * 24 * 31)  # warning and deadline a month away: no alert


def at(minutes):
    return NOW + datetime.timedelta(minutes=minutes)


def make_ticket(*, resolution=FAR, first_response=FAR, **kwargs):
    """resolution / first_response: (warning, deadline) in minutes from NOW."""
    return TicketFactory(
        resolution_warning_at=at(resolution[0]),
        resolution_due_at=at(resolution[1]),
        first_response_warning_at=at(first_response[0]),
        first_response_due_at=at(first_response[1]),
        **kwargs,
    )


def answered():
    return {"first_response_at": at(-30), "first_response_business_minutes": 30}


def raised(now=NOW):
    return {(a.ticket_id, a.kind, a.level) for a in raise_sla_alerts(now)}


# Resolution


def test_resolution_warning_once_the_warning_instant_has_passed():
    ticket = make_ticket(resolution=(-5, 60))

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.WARNING)}


def test_nothing_before_the_warning_instant():
    make_ticket(resolution=(5, 60), first_response=(5, 60))

    assert raised() == set()


def test_warning_instant_equal_to_now_raises_the_warning():
    ticket = make_ticket(resolution=(0, 60))

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.WARNING)}


def test_deadline_equal_to_now_is_a_breach_not_a_warning():
    ticket = make_ticket(resolution=(-60, 0), **answered())

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.BREACHED)}


def test_overdue_ticket_never_alerted_gets_only_the_breach():
    # The job was down during the warning window: no late warning.
    ticket = make_ticket(resolution=(-120, -60), **answered())

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.BREACHED)}


@pytest.mark.parametrize("status", [Status.NEW, Status.OPEN])
def test_resolution_alerts_while_the_clock_runs(status):
    ticket = make_ticket(resolution=(-120, -60), status=status, **answered())

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.BREACHED)}


@pytest.mark.parametrize(
    "kwargs",
    [{"status": Status.PENDING}, {"resolved": True}, {"closed": True}],
    ids=["pending", "resolved", "closed"],
)
def test_no_resolution_alerts_while_the_clock_is_stopped(kwargs):
    make_ticket(resolution=(-120, -60), **answered(), **kwargs)

    assert raised() == set()


# First response


def test_first_response_warning_and_breach():
    warned = make_ticket(first_response=(-5, 60))
    breached = make_ticket(first_response=(-120, -60))

    assert raised() == {
        (warned.pk, AlertKind.FIRST_RESPONSE, AlertLevel.WARNING),
        (breached.pk, AlertKind.FIRST_RESPONSE, AlertLevel.BREACHED),
    }


def test_first_response_deadline_equal_to_now_is_a_breach():
    ticket = make_ticket(first_response=(-60, 0))

    assert raised() == {(ticket.pk, AlertKind.FIRST_RESPONSE, AlertLevel.BREACHED)}


def test_first_response_clock_keeps_running_in_pending():
    ticket = make_ticket(first_response=(-120, -60), status=Status.PENDING)

    assert raised() == {(ticket.pk, AlertKind.FIRST_RESPONSE, AlertLevel.BREACHED)}


def test_no_first_response_alerts_once_answered():
    make_ticket(first_response=(-120, -60), **answered())

    assert raised() == set()


@pytest.mark.parametrize("trait", ["resolved", "closed"])
def test_no_first_response_alerts_for_a_ticket_finished_without_reply(trait):
    # first_response_at stays NULL forever: only the status filter stops it.
    make_ticket(first_response=(-120, -60), **{trait: True})

    assert raised() == set()


# What gets stored and returned


def test_returns_the_inserted_rows_with_the_deadline_not_the_warning_instant():
    ticket = make_ticket(resolution=(-5, 60))

    [alert] = raise_sla_alerts(NOW)

    stored = SlaAlert.objects.get()
    assert alert == RaisedAlert(
        id=stored.pk,
        ticket_id=ticket.pk,
        organization_id=ticket.organization_id,
        kind=AlertKind.RESOLUTION,
        level=AlertLevel.WARNING,
        due_at=at(60),
    )
    assert stored.due_at == ticket.resolution_due_at


def test_one_run_can_raise_every_kind_and_level():
    resolution = make_ticket(resolution=(-5, 60), first_response=(-120, -60))
    first_response = make_ticket(resolution=(-120, -60), first_response=(-5, 60))

    assert raised() == {
        (resolution.pk, AlertKind.RESOLUTION, AlertLevel.WARNING),
        (resolution.pk, AlertKind.FIRST_RESPONSE, AlertLevel.BREACHED),
        (first_response.pk, AlertKind.RESOLUTION, AlertLevel.BREACHED),
        (first_response.pk, AlertKind.FIRST_RESPONSE, AlertLevel.WARNING),
    }


def test_second_run_raises_nothing():
    make_ticket(resolution=(-5, 60), first_response=(-120, -60))
    raise_sla_alerts(NOW)

    assert raise_sla_alerts(NOW) == []
    assert SlaAlert.objects.count() == 2


def test_warned_ticket_gets_the_breach_when_the_deadline_passes():
    ticket = make_ticket(resolution=(-5, 60), **answered())

    assert raised() == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.WARNING)}
    assert raised(at(60)) == {(ticket.pk, AlertKind.RESOLUTION, AlertLevel.BREACHED)}


def test_already_alerted_tickets_do_not_reach_the_conflict():
    # NOT EXISTS filters them out before the INSERT: no identity values wasted.
    make_ticket(resolution=(-5, 60), **answered())
    raise_sla_alerts(NOW)

    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) "
            + sla_alerts.RESOLUTION_WARNING,
            {"now": NOW},
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "Conflicting Tuples: 0" in plan, plan


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        raise_sla_alerts(NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("statement", "index_prefix"),
    [
        (sla_alerts.RESOLUTION_WARNING, "ticket_resolution_"),
        (sla_alerts.RESOLUTION_BREACHED, "ticket_resolution_"),
        (sla_alerts.FIRST_RESPONSE_WARNING, "ticket_first_resp_"),
        (sla_alerts.FIRST_RESPONSE_BREACHED, "ticket_first_resp_"),
    ],
)
def test_statements_can_use_their_partial_indexes(statement, index_prefix):
    # With seq scans disabled the planner takes an index whenever one is usable.
    # A partial index is usable only if the WHERE implies its predicate: if a
    # statement stops repeating it, the plan falls back to a seq scan on ticket.
    # Which of the two indexes of the clock it picks, and whether it reads it with
    # an index scan ("using <index>") or a bitmap scan ("Bitmap Index Scan on
    # <index>"), depends on the statistics (both indexes have the same predicate),
    # so only the clock is checked.
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("EXPLAIN (COSTS OFF) " + statement, {"now": NOW})
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "Seq Scan on ticket" not in plan, plan
    assert re.search(rf"(using|Bitmap Index Scan on) {index_prefix}", plan), plan
