"""Business time across daylight saving changes, case by case.

2026 changes (checked with zoneinfo, not from memory):

- Europe/Madrid: 29 March, 02:00 jumps to 03:00; 25 October, 03:00 back to 02:00
  (02:00-03:00 happens twice).
- America/New_York: 8 March, 02:00 jumps to 03:00; 1 November, 02:00 back to 01:00
  (01:00-02:00 happens twice: not the same hour as in Madrid).
- Australia/Lord_Howe: half-hour changes. 5 April lasts 24.5 h, 4 October 23.5 h.

The rule (business_time.py): a local time that does not exist moves forward by
the length of the gap; one that happens twice is its first occurrence.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.sla.business_time import Schedule, Slot, add_business_minutes, business_minutes_between

MADRID = ZoneInfo("Europe/Madrid")
NEW_YORK = ZoneInfo("America/New_York")
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")


def every_day(tz, *slots):
    return Schedule(timezone=tz, weekly_slots=(tuple(slots),) * 7, holidays=frozenset())


def office(tz):
    """Monday to Friday, 9:00-18:00."""
    return Schedule(
        timezone=tz,
        weekly_slots=((Slot(time(9), time(18)),),) * 5 + ((), ()),
        holidays=frozenset(),
    )


def minutes_on(day, calendar):
    """Business minutes of one local day, from its midnight to the next one.

    Midnight exists on every day used here: the changes happen at night.
    """
    tz = calendar.timezone
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return business_minutes_between(start, end, calendar)


ALWAYS = (Slot(time(0), time(0)),)


@pytest.mark.parametrize(
    ("tz", "day", "expected"),
    [
        (MADRID, date(2026, 3, 29), 23 * 60),
        (MADRID, date(2026, 10, 25), 25 * 60),
        (NEW_YORK, date(2026, 3, 8), 23 * 60),
        (NEW_YORK, date(2026, 11, 1), 25 * 60),
        (LORD_HOWE, date(2026, 4, 5), 24 * 60 + 30),
        (LORD_HOWE, date(2026, 10, 4), 24 * 60 - 30),
    ],
    ids=["madrid-spring", "madrid-autumn", "ny-spring", "ny-autumn", "lh-april", "lh-october"],
)
def test_a_whole_day_lasts_the_real_time(tz, day, expected):
    assert minutes_on(day, every_day(tz, *ALWAYS)) == expected


@pytest.mark.parametrize(
    ("tz", "day", "slot", "expected"),
    [
        # Starts at a time that does not exist: 02:30 becomes 03:30.
        (MADRID, date(2026, 3, 29), Slot(time(2, 30), time(5)), 90),
        (NEW_YORK, date(2026, 3, 8), Slot(time(2, 30), time(5)), 90),
        # Ends at a time that does not exist: 02:30 becomes 03:30 on the new clock,
        # 150 real minutes after midnight, as on any other day.
        (MADRID, date(2026, 3, 29), Slot(time(0), time(2, 30)), 150),
        (NEW_YORK, date(2026, 3, 8), Slot(time(0), time(2, 30)), 150),
        # Starts at a time that happens twice: the first one, so the repeated hour
        # is worked as well.
        (MADRID, date(2026, 10, 25), Slot(time(2, 30), time(5)), 210),
        (NEW_YORK, date(2026, 11, 1), Slot(time(1, 30), time(5)), 270),
        # Ends at a time that happens twice: the first one.
        (MADRID, date(2026, 10, 25), Slot(time(0), time(2, 30)), 150),
        (NEW_YORK, date(2026, 11, 1), Slot(time(0), time(1, 30)), 90),
    ],
    ids=[
        "madrid-starts-in-gap",
        "ny-starts-in-gap",
        "madrid-ends-in-gap",
        "ny-ends-in-gap",
        "madrid-starts-in-repeated-hour",
        "ny-starts-in-repeated-hour",
        "madrid-ends-in-repeated-hour",
        "ny-ends-in-repeated-hour",
    ],
)
def test_slot_with_a_missing_or_repeated_end(tz, day, slot, expected):
    assert minutes_on(day, every_day(tz, slot)) == expected


@pytest.mark.parametrize(
    ("tz", "friday", "monday_10_utc"),
    [
        # 9:00 local is 08:00 UTC in Madrid in winter, 07:00 UTC in summer.
        (MADRID, date(2026, 3, 27), datetime(2026, 3, 30, 8, 0, tzinfo=UTC)),
        (MADRID, date(2026, 10, 23), datetime(2026, 10, 26, 9, 0, tzinfo=UTC)),
        (NEW_YORK, date(2026, 3, 6), datetime(2026, 3, 9, 14, 0, tzinfo=UTC)),
        (NEW_YORK, date(2026, 10, 30), datetime(2026, 11, 2, 15, 0, tzinfo=UTC)),
    ],
    ids=["madrid-spring", "madrid-autumn", "ny-spring", "ny-autumn"],
)
def test_office_hours_follow_the_local_clock_across_the_change(tz, friday, monday_10_utc):
    calendar = office(tz)
    friday_17 = datetime.combine(friday, time(17), tzinfo=tz)

    # 1 h on Friday and 1 h on Monday: 10:00 local, whatever the UTC offset.
    due = add_business_minutes(friday_17, 120, calendar)

    assert due == monday_10_utc
    assert due.astimezone(tz).time() == time(10)
    assert business_minutes_between(friday_17, due, calendar) == 120


def test_a_deadline_inside_the_repeated_hour():
    # Madrid, 25 October, working 00:00-05:00. From 01:00, 90 minutes: 01:00-02:00
    # CEST (60) and the first half of the repeated hour, 02:00-02:30 CEST (30).
    calendar = every_day(MADRID, Slot(time(0), time(5)))
    start = datetime(2026, 10, 25, 1, 0, tzinfo=MADRID)

    due = add_business_minutes(start, 90, calendar)

    assert due == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST, the first one.
    assert business_minutes_between(start, due, calendar) == 90


def test_a_slot_inside_the_skipped_hour_does_not_exist_that_day():
    # Found by the property-based tests. New York, Sundays 02:01-03:00: on 8 March
    # 02:01 does not exist and becomes 03:01, after the end. The slot is empty
    # that day; before the fix, adding backwards over it added a minute instead of
    # consuming one.
    calendar = every_day(NEW_YORK, Slot(time(2, 1), time(3)))
    monday = datetime(2026, 3, 9, 0, 0, tzinfo=UTC)  # Sunday 8 March, 20:00 EDT.

    assert minutes_on(date(2026, 3, 8), calendar) == 0
    start_of_last_minute = add_business_minutes(monday, -1, calendar)
    # The previous working minute is 02:59-03:00 EDT on Saturday 7 March.
    assert start_of_last_minute == datetime(2026, 3, 7, 7, 59, tzinfo=UTC)
    assert business_minutes_between(monday, start_of_last_minute, calendar) == -1
