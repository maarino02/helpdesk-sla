"""What follows from the deadlines: the warning instants and the SLA state.

Every service that writes a deadline (creation, transitions, triage, calendar
recalculation) calls refresh_warnings afterwards, with the schedule it used, so
the warning instants never fall out of step with the deadlines.
"""

from datetime import datetime

from django.db import models

from apps.sla.business_time import Schedule, business_minutes_between, warning_instant
from apps.tickets.models import RESOLVED_STATUSES, Status, Ticket

WARNING_FIELDS = ["first_response_warning_at", "resolution_warning_at"]


def refresh_warnings(ticket: Ticket, schedule: Schedule) -> list[str]:
    """Recompute both warning instants from the ticket's deadlines. Returns the
    fields set, for save(update_fields=...).

    The resolution budget is the ticket's own target. The first response clock
    never pauses, so its budget is the business time from creation to its
    deadline: exact, and independent of later edits of the policy.
    """
    ticket.resolution_warning_at = warning_instant(
        ticket.resolution_due_at, ticket.resolution_target_minutes, schedule
    )
    first_response_budget = business_minutes_between(
        ticket.created_at, ticket.first_response_due_at, schedule
    )
    ticket.first_response_warning_at = warning_instant(
        ticket.first_response_due_at, first_response_budget, schedule
    )
    return list(WARNING_FIELDS)


class SlaState(models.TextChoices):
    ON_TRACK = "on_track", "En plazo"
    AT_RISK = "at_risk", "En riesgo"
    PAUSED = "paused", "En pausa"
    BREACHED = "breached", "Incumplido"
    MET = "met", "Cumplido"


def sla_state(ticket: Ticket, now: datetime) -> str:
    """Where the ticket stands against its SLA at `now`, for the web.

    The rules of the alert job and the dashboard, so the three never disagree:
    a deadline equal to the instant counts as breached, and the resolution clock
    of a pending ticket stopped at sla_paused_at. A breach, once it happened,
    stays: a ticket answered late is breached even if its resolution is on time.
    """
    answered = ticket.first_response_at
    if ticket.status in RESOLVED_STATUSES:
        # Finished: the outcome. Never answered publicly counts as answered when
        # it was resolved.
        late = ticket.resolved_at >= ticket.resolution_due_at or (
            (answered or ticket.resolved_at) >= ticket.first_response_due_at
        )
        return SlaState.BREACHED if late else SlaState.MET

    clock = ticket.sla_paused_at or now  # Pending: the clock stopped there.
    if (
        ticket.resolution_due_at <= clock
        or (answered is None and ticket.first_response_due_at <= now)
        or (answered is not None and answered >= ticket.first_response_due_at)
    ):
        return SlaState.BREACHED

    # The first response clock never pauses; the resolution one only runs in
    # new and open.
    if (answered is None and ticket.first_response_warning_at <= now) or (
        ticket.status in (Status.NEW, Status.OPEN) and ticket.resolution_warning_at <= now
    ):
        return SlaState.AT_RISK
    if ticket.status == Status.PENDING:
        return SlaState.PAUSED
    return SlaState.ON_TRACK
