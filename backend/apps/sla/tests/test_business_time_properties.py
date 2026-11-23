"""Properties of business time that must hold for any calendar and any instant.

hypothesis generates the calendars (time zones with daylight saving changes and
odd offsets, random non-overlapping slots, holidays), the instants and the
minutes, and shrinks any failure to a minimal case. The hand-written cases are in
test_business_time.py and test_business_time_dst.py.

Instants are aligned to the minute: slot boundaries are too, in every zone used
here (Chatham is +12:45, Lord Howe changes by 30 minutes), so sums of business
minutes are exact and additivity holds without truncation getting in the way.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import example, given
from hypothesis import strategies as st

from apps.sla.business_time import (
    Schedule,
    Slot,
    add_business_minutes,
    business_minutes_between,
    reproject,
)

ZONES = [
    "Europe/Madrid",
    "America/New_York",
    "UTC",
    "Asia/Kolkata",  # +05:30, no daylight saving.
    "Australia/Lord_Howe",  # Changes by 30 minutes.
    "Pacific/Chatham",  # +12:45 / +13:45.
]
FIRST_DAY, LAST_DAY = date(2026, 1, 1), date(2028, 12, 31)
ONE_MINUTE = timedelta(minutes=1)


def _time(minute_of_day):
    # 1440 is the end of the day: 00:00 as an end means 24:00.
    return time(0) if minute_of_day == 1440 else time(minute_of_day // 60, minute_of_day % 60)


@st.composite
def day_slots(draw):
    """Non-overlapping slots of one day, as the database guarantees: sorted
    distinct boundaries in minutes, taken in pairs."""
    bounds = sorted(draw(st.sets(st.integers(0, 1440), max_size=6)))
    return tuple(
        Slot(_time(start), _time(end))
        for start, end in zip(bounds[::2], bounds[1::2], strict=False)
    )


@st.composite
def schedules(draw):
    weekly = tuple(draw(day_slots()) for _ in range(7))
    if not any(weekly):
        weekly = ((Slot(time(9), time(18)),),) + weekly[1:]
    return Schedule(
        timezone=ZoneInfo(draw(st.sampled_from(ZONES))),
        weekly_slots=weekly,
        holidays=frozenset(draw(st.lists(st.dates(FIRST_DAY, LAST_DAY), max_size=10))),
    )


def _weekly_minutes(calendar):
    return sum(
        (end.hour * 60 + end.minute or 1440) - (start.hour * 60 + start.minute)
        for day in calendar.weekly_slots
        for start, end in day
    )


def minutes_for(calendar):
    """At most 20 weeks of the calendar's working time (and 20000 minutes): a
    sparse calendar must not need more than MAX_SEARCH_DAYS to find them."""
    limit = min(20000, 20 * _weekly_minutes(calendar))
    return st.integers(-limit, limit)


instants = st.datetimes(
    min_value=datetime(2026, 1, 1),
    max_value=datetime(2028, 12, 31),
    timezones=st.just(UTC),
).map(lambda value: value.replace(second=0, microsecond=0))


# The minimal case hypothesis found: a slot entirely inside the skipped hour of
# New York's spring change (8 March 2026) became an inverted interval.
NEW_YORK_GAP = Schedule(
    timezone=ZoneInfo("America/New_York"),
    weekly_slots=((),) * 6 + ((Slot(time(2, 1), time(3)),),),
    holidays=frozenset(),
)


@given(schedules(), instants, st.integers(-20000, 20000))
@example(NEW_YORK_GAP, datetime(2026, 3, 9, 0, 0, tzinfo=UTC), -1)
def test_add_and_between_are_inverse(calendar, start, any_minutes):
    limit = min(20000, 20 * _weekly_minutes(calendar))
    minutes = max(-limit, min(limit, any_minutes))

    end = add_business_minutes(start, minutes, calendar)

    assert business_minutes_between(start, end, calendar) == minutes


@given(schedules(), instants, instants)
def test_between_is_antisymmetric(calendar, a, b):
    assert business_minutes_between(a, b, calendar) == -business_minutes_between(b, a, calendar)


@given(schedules(), instants, instants, instants)
def test_between_is_additive(calendar, x, y, z):
    a, b, c = sorted([x, y, z])

    assert business_minutes_between(a, c, calendar) == (
        business_minutes_between(a, b, calendar) + business_minutes_between(b, c, calendar)
    )


@given(schedules(), instants, st.data())
def test_add_is_strictly_increasing(calendar, start, data):
    n1, n2 = sorted(data.draw(st.lists(minutes_for(calendar), min_size=2, max_size=2, unique=True)))

    assert add_business_minutes(start, n1, calendar) < add_business_minutes(start, n2, calendar)


@given(schedules(), instants, st.data())
def test_result_is_at_the_edge_of_a_working_minute(calendar, start, data):
    minutes = data.draw(minutes_for(calendar).filter(lambda n: n != 0))

    result = add_business_minutes(start, minutes, calendar)

    # Going forward, the last minute consumed ends at the result; going backwards,
    # the first one starts there.
    if minutes > 0:
        assert business_minutes_between(result - ONE_MINUTE, result, calendar) == 1
    else:
        assert business_minutes_between(result, result + ONE_MINUTE, calendar) == 1


@given(schedules(), instants, st.data(), st.sampled_from([1, -1]))
def test_adding_in_two_steps_in_the_same_direction(calendar, start, data, sign):
    # Half the limit each, so that the total stays within the search limit too.
    half = min(20000, 20 * _weekly_minutes(calendar)) // 2
    n, m = (sign * data.draw(st.integers(0, half)) for _ in range(2))

    in_two_steps = add_business_minutes(add_business_minutes(start, n, calendar), m, calendar)

    assert in_two_steps == add_business_minutes(start, n + m, calendar)


@given(schedules(), instants, instants)
def test_never_more_than_real_time(calendar, a, b):
    real = int(abs(b - a) / ONE_MINUTE)

    assert abs(business_minutes_between(a, b, calendar)) <= real


@given(st.sampled_from(ZONES), instants, st.integers(-20000, 20000))
def test_always_open_is_real_time(zone, start, minutes):
    calendar = Schedule(
        timezone=ZoneInfo(zone),
        weekly_slots=((Slot(time(0), time(0)),),) * 7,
        holidays=frozenset(),
    )

    end = add_business_minutes(start, minutes, calendar)

    assert end == start + minutes * ONE_MINUTE
    assert business_minutes_between(start, end, calendar) == minutes


@given(schedules(), instants, st.data())
def test_reprojecting_to_the_same_calendar_keeps_the_deadline(calendar, anchor, data):
    # Deadlines come from add_business_minutes, so they sit at the edge of a
    # working minute and measuring them back gives the same instant.
    limit = min(20000, 20 * _weekly_minutes(calendar))
    due = add_business_minutes(anchor, data.draw(st.integers(0, limit)), calendar)

    assert reproject(anchor, due, calendar, calendar) == due
