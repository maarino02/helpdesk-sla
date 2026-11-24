"""sla_state(): where a ticket stands against its SLA, as the web shows it.

A pure function of the ticket's fields and the instant: unsaved tickets are
enough. The boundaries are those of the alert job and the dashboard.
"""

import datetime as dt

import pytest

from apps.tickets.models import Status, Ticket
from apps.tickets.services.deadlines import SlaState, sla_state

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)


def at(minutes):
    """NOW plus `minutes` (negative: in the past)."""
    return NOW + dt.timedelta(minutes=minutes)


def ticket(status=Status.OPEN, **fields):
    """Answered on time ten minutes ago, every deadline and warning in the future."""
    values = {
        "status": status,
        "first_response_at": at(-10),
        "first_response_warning_at": at(-30),
        "first_response_due_at": at(-5),
        "resolution_warning_at": at(60),
        "resolution_due_at": at(120),
        "sla_paused_at": at(-1) if status in (Status.PENDING, Status.RESOLVED) else None,
        "resolved_at": at(-1) if status in (Status.RESOLVED, Status.CLOSED) else None,
    }
    return Ticket(**{**values, **fields})


UNANSWERED = {"first_response_at": None, "first_response_warning_at": at(30)}


@pytest.mark.parametrize(
    ("case", "fields", "expected"),
    [
        # Running clock.
        (
            "new, nothing due",
            {"status": Status.NEW, **UNANSWERED, "first_response_due_at": at(60)},
            SlaState.ON_TRACK,
        ),
        (
            "first response warning reached",
            {
                "status": Status.NEW,
                **UNANSWERED,
                "first_response_warning_at": NOW,
                "first_response_due_at": at(60),
            },
            SlaState.AT_RISK,
        ),
        (
            "first response due now",
            {"status": Status.NEW, **UNANSWERED, "first_response_due_at": NOW},
            SlaState.BREACHED,
        ),
        ("answered on time", {}, SlaState.ON_TRACK),
        ("answered late", {"first_response_at": at(-5)}, SlaState.BREACHED),
        ("resolution warning reached", {"resolution_warning_at": NOW}, SlaState.AT_RISK),
        ("resolution due now", {"resolution_due_at": NOW}, SlaState.BREACHED),
        # Paused clock.
        (
            "pending in time",
            {"status": Status.PENDING, "resolution_warning_at": at(-60)},
            SlaState.PAUSED,
        ),
        (
            "pending, paused after the deadline",
            {"status": Status.PENDING, "resolution_due_at": at(-60), "sla_paused_at": at(-30)},
            SlaState.BREACHED,
        ),
        (
            "pending, paused before the deadline",
            {"status": Status.PENDING, "resolution_due_at": at(-60), "sla_paused_at": at(-90)},
            SlaState.PAUSED,
        ),
        (
            "pending, first response clock runs",
            {
                "status": Status.PENDING,
                **UNANSWERED,
                "first_response_warning_at": at(-1),
                "first_response_due_at": at(60),
            },
            SlaState.AT_RISK,
        ),
        # Finished: the outcome.
        ("resolved on time", {"status": Status.RESOLVED}, SlaState.MET),
        (
            "resolved at the deadline",
            {"status": Status.RESOLVED, "resolved_at": at(120)},
            SlaState.BREACHED,
        ),
        (
            "closed, answered late",
            {"status": Status.CLOSED, "first_response_at": at(-5)},
            SlaState.BREACHED,
        ),
        (
            "resolved unanswered, in time",
            {"status": Status.RESOLVED, **UNANSWERED, "first_response_due_at": at(60)},
            SlaState.MET,
        ),
        (
            "resolved unanswered, after its due",
            {"status": Status.RESOLVED, **UNANSWERED, "first_response_due_at": at(-2)},
            SlaState.BREACHED,
        ),
    ],
)
def test_sla_state(case, fields, expected):
    assert sla_state(ticket(**fields), NOW) == expected
