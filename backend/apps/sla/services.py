"""SLA services: the database side of the SLA calculation.

They load what the pure functions in business_time.py need and apply them.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

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


def lock_and_snapshot(calendar_id: int) -> Schedule:
    """Lock a calendar row and return its schedule as it is now.

    Call it before changing the calendar, inside the transaction that changes it:
    the snapshot is what the deadlines are reprojected from. With the row locked,
    two edits of the same calendar are serialized, so the second one takes its
    snapshot after the first has committed. Without the lock, both would start from
    the same snapshot and the second would reproject from a state that no longer
    exists.
    """
    calendar = BusinessCalendar.objects.select_for_update().get(pk=calendar_id)
    return load_schedule(calendar)


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
