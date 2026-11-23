"""SLA services: the database side of the SLA calculation.

They load what the pure functions in business_time.py need and apply them.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import F, Q

from .business_time import Schedule, Slot, add_business_minutes
from .models import BusinessCalendar, SlaPolicy


class MissingSlaPolicy(Exception):
    """No policy for a priority, neither the organization's nor the default one.

    A configuration error: every priority needs a default policy.
    """


@dataclass(frozen=True)
class DueDates:
    first_response: datetime
    resolution: datetime


def load_schedule(calendar: BusinessCalendar) -> Schedule:
    """Build the immutable schedule of a calendar (two queries)."""
    weekly_slots = [[] for _ in range(7)]
    for weekday, start, end in calendar.business_hours.order_by(
        "weekday", "start_time"
    ).values_list("weekday", "start_time", "end_time"):
        weekly_slots[weekday].append(Slot(start, end))
    holidays = frozenset(calendar.holidays.values_list("date", flat=True))
    return Schedule(
        timezone=ZoneInfo(calendar.timezone),
        weekly_slots=tuple(tuple(day) for day in weekly_slots),
        holidays=holidays,
    )


def load_organization_schedule(organization_id: int) -> Schedule:
    """The schedule of an organization's calendar (three queries)."""
    calendar = BusinessCalendar.objects.get(organizations__id=organization_id)
    return load_schedule(calendar)


# Advisory lock namespace for business calendars: locks are (namespace, calendar
# id). No other code may use namespace 1 for anything else.
CALENDAR_LOCK_NAMESPACE = 1


def lock_and_snapshot(calendar_id: int) -> Schedule:
    """Take the calendar's exclusive lock and return its schedule as it is now.

    Call it before changing the calendar, inside the transaction that changes it:
    the snapshot is what the deadlines are reprojected from. Two edits of the same
    calendar are serialized, so the second takes its snapshot after the first has
    committed; ticket creation holds the shared lock while it computes deadlines
    (share_calendar_lock), so it never uses a schedule that is about to change
    without being included in the recalculation.

    An advisory lock rather than SELECT ... FOR UPDATE on the row: row locks have
    to pass the UPDATE policies of row-level security, which only admins do on
    catalogs, so a client's FOR SHARE would silently lock nothing. Advisory locks
    are not tied to rows and RLS does not apply to them. ADR-0005.
    """
    _advisory_lock("pg_advisory_xact_lock", calendar_id)
    return load_calendar_schedule(calendar_id)


def share_calendar_lock(calendar_id: int) -> None:
    """Take the calendar's shared lock: compatible with other holders of the
    shared lock (concurrent ticket creations), exclusive with lock_and_snapshot."""
    _advisory_lock("pg_advisory_xact_lock_shared", calendar_id)


def load_calendar_schedule(calendar_id: int) -> Schedule:
    """The schedule of a calendar read now (three queries)."""
    return load_schedule(BusinessCalendar.objects.get(pk=calendar_id))


def _advisory_lock(function: str, calendar_id: int) -> None:
    if not connection.in_atomic_block:
        # A transaction-level lock taken in autocommit mode would be released at
        # the end of this very statement. Fail loudly instead.
        raise RuntimeError("Calendar locks need an open transaction.")
    with connection.cursor() as cursor:
        # The function name comes from this module, never from input.
        cursor.execute(f"SELECT {function}(%s, %s)", [CALENDAR_LOCK_NAMESPACE, calendar_id])


def resolve_policy(organization, priority: str) -> SlaPolicy:
    """The organization's policy for a priority, or the default one.

    One query: both candidates match the WHERE, and NULLS LAST puts the
    organization's row (if any) before the default one (organization_id NULL).
    """
    policy = (
        SlaPolicy.objects.filter(
            Q(organization=organization) | Q(organization__isnull=True),
            priority=priority,
        )
        .order_by(F("organization").asc(nulls_last=True))
        .first()
    )
    if policy is None:
        raise MissingSlaPolicy(f"No SLA policy for priority {priority!r}.")
    return policy


def compute_due_dates(policy: SlaPolicy, schedule: Schedule, start: datetime) -> DueDates:
    """Both deadlines of a ticket created at `start`."""
    return DueDates(
        first_response=add_business_minutes(start, policy.first_response_minutes, schedule),
        resolution=add_business_minutes(start, policy.resolution_minutes, schedule),
    )
