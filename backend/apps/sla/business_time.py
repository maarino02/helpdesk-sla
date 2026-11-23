"""Business-time arithmetic: pure functions, no database access (ADR-0002).

A business minute is a real minute inside a working slot of the calendar, in the
calendar's time zone, on a day that is not a holiday. Everything is computed in
UTC: in Python, subtracting two datetimes that share the same tzinfo gives the
wall-clock difference and ignores daylight saving changes (on the spring change
in Madrid, 00:00 to 04:00 would give 4 hours instead of the 3 that pass).

Daylight saving changes follow the rule of java.time and of Python's fold=0: a
local time that does not exist (spring) moves forward by the length of the gap,
and one that happens twice (autumn) is its first occurrence. So a 01:00-04:00
slot lasts 2 real hours on the spring change and 4 on the autumn change.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

# As an end time, 00:00 means midnight at the end of the day (24:00). The same
# convention as apps.sla.models.END_OF_DAY, repeated so this module stays free of
# Django imports.
END_OF_DAY = time(0)

# How far add_business_minutes searches before giving up: a calendar whose slots
# are all covered by holidays for years is a configuration error that must be
# visible, not an endless loop.
MAX_SEARCH_DAYS = 3660


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
    The result is the instant closest to `start`: going forward, a deadline used
    up exactly at the end of a slot is that end (18:00), not the start of the
    next slot (09:00 the next day); going backwards, the start of the slot.
    Returns an aware datetime in UTC.
    """
    _require_aware(start)
    start = start.astimezone(UTC)
    if minutes == 0:
        return start
    if not any(calendar.weekly_slots):
        raise ValueError("El calendario no tiene horario laboral.")

    forward = minutes > 0
    remaining = timedelta(minutes=abs(minutes))
    first_day = start.astimezone(calendar.timezone).date()
    step = timedelta(days=1 if forward else -1)

    for offset in range(MAX_SEARCH_DAYS):
        intervals = _working_intervals(calendar, first_day + offset * step)
        for begin, end in intervals if forward else reversed(intervals):
            if forward:
                if end <= start:
                    continue
                begin = max(begin, start)
                available = end - begin
                if available >= remaining:
                    return begin + remaining
            else:
                if begin >= start:
                    continue
                end = min(end, start)
                available = end - begin
                if available >= remaining:
                    return end - remaining
            remaining -= available

    raise ValueError(
        f"No hay horario laboral suficiente en {MAX_SEARCH_DAYS} días: "
        "revisa las franjas y los festivos del calendario."
    )


def business_minutes_between(start: datetime, end: datetime, calendar: Schedule) -> int:
    """Business minutes from `start` to `end`, negative if `end` comes first.

    The sign lets callers measure time left on a deadline that has already
    expired. Partial minutes are truncated towards zero, so swapping the
    arguments only changes the sign.
    """
    _require_aware(start)
    _require_aware(end)
    if end < start:
        return -business_minutes_between(end, start, calendar)
    start, end = start.astimezone(UTC), end.astimezone(UTC)

    total = timedelta(0)
    day = start.astimezone(calendar.timezone).date()
    last_day = end.astimezone(calendar.timezone).date()
    while day <= last_day:
        for begin, finish in _working_intervals(calendar, day):
            overlap = min(finish, end) - max(begin, start)
            if overlap > timedelta(0):
                total += overlap
        day += timedelta(days=1)
    return int(total / timedelta(minutes=1))


def _working_intervals(calendar: Schedule, day: date) -> list[tuple[datetime, datetime]]:
    """The working intervals of one local date, in UTC and in order.

    Slots never overlap and never go past 24:00 (database constraints), so the
    intervals of consecutive days follow each other in order too.
    """
    if day in calendar.holidays:
        return []
    intervals = []
    for slot in calendar.weekly_slots[day.weekday()]:
        # datetime.combine leaves fold=0: the java.time rule described above.
        begin = datetime.combine(day, slot.start, tzinfo=calendar.timezone)
        end_day = day + timedelta(days=1) if slot.end == END_OF_DAY else day
        end = datetime.combine(end_day, slot.end, tzinfo=calendar.timezone)
        intervals.append((begin.astimezone(UTC), end.astimezone(UTC)))
    return intervals


def _require_aware(value: datetime) -> None:
    # A naive datetime has no time zone: comparing it with business hours in the
    # calendar's zone would silently give wrong results.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Expected an aware datetime, got {value!r}.")
