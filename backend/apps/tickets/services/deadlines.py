"""Derived SLA instants that follow the deadlines.

Every service that writes a deadline (creation, transitions, triage, calendar
recalculation) calls refresh_warnings afterwards, with the schedule it used, so
the warning instants never fall out of step with the deadlines.
"""

from apps.sla.business_time import Schedule, business_minutes_between, warning_instant
from apps.tickets.models import Ticket

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
