"""Business-time arithmetic: pure functions, no database access (ADR-0002).

The current implementation is provisional: it counts wall-clock minutes and
ignores the schedule. The signatures are final, so callers do not change when the
real calculation (working slots, holidays, time zones) replaces it.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo


class Slot(NamedTuple):
    """A working time slot in local time, half-open: [start, end).

    An end of 00:00 means midnight at the end of the day (24:00).
    """

    start: time
    end: time


@dataclass(frozen=True)
class Schedule:
    """A business calendar as an immutable value, built by sla.services.load_schedule.

    Plain values instead of the model: the functions below stay pure and their
    tests need no database.
    """

    timezone: ZoneInfo
    # Indexed by weekday (0 = Monday, as date.weekday()); each day sorted by start.
    weekly_slots: tuple[tuple[Slot, ...], ...]
    # Local dates in the schedule's time zone.
    holidays: frozenset[date]


def add_business_minutes(start: datetime, minutes: int, calendar: Schedule) -> datetime:
    """The instant reached after `minutes` business minutes from `start`.

    Negative minutes go backwards, e.g. when a priority change shortens a deadline.
    """
    _require_aware(start)
    return start + timedelta(minutes=minutes)


def business_minutes_between(start: datetime, end: datetime, calendar: Schedule) -> int:
    """Business minutes from `start` to `end`, negative if `end` comes first.

    The sign lets callers measure time left on a deadline that has already
    expired. Partial minutes are truncated towards zero, so swapping the
    arguments only changes the sign.
    """
    _require_aware(start)
    _require_aware(end)
    return int((end - start) / timedelta(minutes=1))


def _require_aware(value: datetime) -> None:
    # A naive datetime has no time zone: comparing it with business hours in the
    # calendar's zone would silently give wrong results.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Expected an aware datetime, got {value!r}.")
