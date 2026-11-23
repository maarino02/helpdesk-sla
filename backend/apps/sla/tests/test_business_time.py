"""Unit tests of the business-time functions: no database.

They pin the provisional wall-clock behaviour; the values change when the real
calculation arrives, the properties (sign, symmetry, aware datetimes) do not.
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from apps.sla.business_time import (
    Schedule,
    Slot,
    add_business_minutes,
    business_minutes_between,
)

SCHEDULE = Schedule(
    timezone=ZoneInfo("Europe/Madrid"),
    weekly_slots=tuple((Slot(time(9), time(18)),) if day < 5 else () for day in range(7)),
    holidays=frozenset(),
)
MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def test_add_business_minutes():
    assert add_business_minutes(MONDAY_10, 90, SCHEDULE) == datetime(
        2026, 11, 23, 11, 30, tzinfo=UTC
    )


def test_add_negative_minutes_goes_backwards():
    assert add_business_minutes(MONDAY_10, -30, SCHEDULE) == datetime(
        2026, 11, 23, 9, 30, tzinfo=UTC
    )


def test_minutes_between():
    end = datetime(2026, 11, 23, 12, 15, tzinfo=UTC)

    assert business_minutes_between(MONDAY_10, end, SCHEDULE) == 135


def test_minutes_between_is_negative_when_end_comes_first():
    earlier = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)

    assert business_minutes_between(MONDAY_10, earlier, SCHEDULE) == -60


@pytest.mark.parametrize("seconds", [59, 61, 119])
def test_partial_minutes_truncate_towards_zero(seconds):
    end = MONDAY_10.replace(minute=seconds // 60, second=seconds % 60)
    start = MONDAY_10.replace(minute=0)
    forward = business_minutes_between(start, end, SCHEDULE)

    assert forward == seconds // 60
    assert business_minutes_between(end, start, SCHEDULE) == -forward


def test_add_and_between_are_inverse():
    end = add_business_minutes(MONDAY_10, 500, SCHEDULE)

    assert business_minutes_between(MONDAY_10, end, SCHEDULE) == 500


def test_naive_datetimes_are_rejected():
    naive = datetime(2026, 11, 23, 10, 0)

    with pytest.raises(ValueError, match="aware"):
        add_business_minutes(naive, 10, SCHEDULE)
    with pytest.raises(ValueError, match="aware"):
        business_minutes_between(MONDAY_10, naive, SCHEDULE)


def test_schedule_is_immutable():
    with pytest.raises(AttributeError):
        SCHEDULE.holidays = frozenset()
