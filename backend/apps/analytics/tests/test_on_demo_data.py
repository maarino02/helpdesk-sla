"""The metrics on a seeded history, cross-checked against the ORM.

The hand-built datasets of the other tests check each rule; this checks that the
queries also run on realistic data (every status, pauses, reopenings, several
clients and calendars) and agree with a count made another way.
"""

import datetime as dt
import statistics
from collections import defaultdict
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.db.models import F

from apps.analytics.period import Period
from apps.analytics.queries import run
from apps.demo import reset
from apps.demo.seed import seed_demo
from apps.sla.models import Priority
from apps.tickets.models import Ticket

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)
PERIOD = Period(dt.date(2026, 5, 1), dt.date(2026, 11, 24), None, ZoneInfo("Europe/Madrid"))


@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker):
    """Seeded once and committed; emptied at the end for the tests that follow."""
    with django_db_blocker.unblock():
        seed_demo(password="x", tickets=60, seed=5, now=NOW)
        yield
        reset.truncate(connection)


def test_sla_compliance_totals_match_the_tickets(seeded):
    params = PERIOD.params()
    rows = run("sla_compliance", {**params, "priorities": list(Priority.values)})
    totals = [row for row in rows if row["priority"] == "all"]

    resolved = Ticket.objects.filter(
        resolved_at__gte=params["start"], resolved_at__lt=params["end"]
    )
    answered = Ticket.objects.filter(
        first_response_at__gte=params["start"], first_response_at__lt=params["end"]
    )
    assert sum(row["resolutions"] for row in totals) == resolved.count() > 0
    assert sum(row["resolutions_met"] for row in totals) == (
        resolved.filter(resolved_at__lt=F("resolution_due_at")).count()
    )
    assert sum(row["first_responses"] for row in totals) == answered.count() > 0
    assert sum(row["first_responses_met"] for row in totals) == (
        answered.filter(first_response_at__lt=F("first_response_due_at")).count()
    )
    # The rows per priority add up to the month's total.
    for total in totals:
        month = [r for r in rows if r["month"] == total["month"] and r["priority"] != "all"]
        assert sum(r["resolutions"] for r in month) == total["resolutions"]


def test_response_time_medians_match_python(seeded):
    params = PERIOD.params()
    rows = run("response_times", {**params, "priorities": list(Priority.values)})
    zone = PERIOD.time_zone
    by_month = defaultdict(list)
    for resolved_at, minutes in Ticket.objects.filter(
        resolved_at__gte=params["start"], resolved_at__lt=params["end"]
    ).values_list("resolved_at", "resolution_business_minutes"):
        by_month[resolved_at.astimezone(zone).date().replace(day=1)].append(minutes)

    totals = {row["month"]: row for row in rows if row["priority"] == "all"}
    assert by_month
    for month, minutes in by_month.items():
        assert totals[month]["resolutions"] == len(minutes)
        assert totals[month]["resolution_median"] == round(statistics.median(minutes), 1)
