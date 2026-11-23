"""Unit tests of the business-time functions: no database.

Calendars are written in Madrid local time, Monday to Friday 9:00-18:00 unless a
test says otherwise. November 2026: Monday 23 and 30, Friday 27, weekend 28-29.
The full daylight saving battery and the property-based tests are in
test_business_time_dst.py and test_business_time_properties.py.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.sla import business_time
from apps.sla.business_time import (
    Schedule,
    Slot,
    add_business_minutes,
    business_minutes_between,
)

MADRID = ZoneInfo("Europe/Madrid")
NINE_TO_SIX = (Slot(time(9), time(18)),)


def schedule(weekdays=NINE_TO_SIX, weekend=(), holidays=(), days=None, tz=MADRID):
    """Mon-Fri `weekdays`, Sat-Sun `weekend`; `days` overrides weekday by weekday."""
    weekly = [tuple(weekdays)] * 5 + [tuple(weekend)] * 2
    for weekday, slots in (days or {}).items():
        weekly[weekday] = tuple(slots)
    return Schedule(timezone=tz, weekly_slots=tuple(weekly), holidays=frozenset(holidays))


def madrid(day, hour, minute=0, second=0, month=11):
    return datetime(2026, month, day, hour, minute, second, tzinfo=MADRID)


OFFICE = schedule()
ALWAYS_OPEN = schedule(weekdays=(Slot(time(0), time(0)),), weekend=(Slot(time(0), time(0)),))


class TestAdd:
    def test_example_of_the_adr(self):
        # Friday 17:30 + 4 h: 30 minutes on Friday and 3.5 h on Monday.
        assert add_business_minutes(madrid(27, 17, 30), 240, OFFICE) == madrid(30, 12, 30)

    def test_within_one_slot(self):
        assert add_business_minutes(madrid(23, 10), 90, OFFICE) == madrid(23, 11, 30)

    def test_created_before_opening_starts_at_opening(self):
        assert add_business_minutes(madrid(30, 7), 30, OFFICE) == madrid(30, 9, 30)

    def test_created_on_the_weekend_starts_on_monday(self):
        assert add_business_minutes(madrid(28, 10), 60, OFFICE) == madrid(30, 10)

    def test_holiday_is_skipped(self):
        # Tuesday 8 December is a holiday: from Monday 17:00, 1 h that day, 1 h on Wednesday.
        calendar = schedule(holidays=[date(2026, 12, 8)])

        result = add_business_minutes(madrid(7, 17, month=12), 120, calendar)

        assert result == madrid(9, 10, month=12)

    def test_split_shift_skips_the_lunch_break(self):
        calendar = schedule(weekdays=[Slot(time(9), time(14)), Slot(time(15), time(18))])

        assert add_business_minutes(madrid(23, 13, 30), 60, calendar) == madrid(23, 15, 30)

    def test_night_shift_until_midnight(self):
        # Monday 22:00-24:00 and Tuesday 00:00-06:00: one shift across midnight.
        calendar = schedule(
            weekdays=(),
            days={0: [Slot(time(22), time(0))], 1: [Slot(time(0), time(6))]},
        )

        assert add_business_minutes(madrid(23, 23), 120, calendar) == madrid(24, 1)

    def test_always_open_is_wall_clock_time(self):
        assert add_business_minutes(madrid(27, 17, 30), 3 * 24 * 60, ALWAYS_OPEN) == madrid(
            30, 17, 30
        )

    def test_used_up_at_the_end_of_a_slot_stays_there(self):
        # 18:00, not 09:00 the next day: the instant closest to the start.
        assert add_business_minutes(madrid(23, 9), 9 * 60, OFFICE) == madrid(23, 18)

    def test_zero_minutes_is_the_same_instant(self):
        assert add_business_minutes(madrid(28, 10), 0, OFFICE) == madrid(28, 10)

    def test_result_is_in_utc_whatever_the_input_zone(self):
        result = add_business_minutes(madrid(23, 10).astimezone(UTC), 60, OFFICE)

        assert result.tzinfo is UTC
        assert result == madrid(23, 11)


class TestAddBackwards:
    def test_crosses_the_weekend(self):
        assert add_business_minutes(madrid(30, 10), -120, OFFICE) == madrid(27, 17)

    def test_used_up_at_the_start_of_a_slot_stays_there(self):
        # 09:00, not 18:00 the day before: the instant closest to the start.
        assert add_business_minutes(madrid(30, 10), -60, OFFICE) == madrid(30, 9)

    def test_from_outside_working_hours(self):
        assert add_business_minutes(madrid(28, 12), -60, OFFICE) == madrid(27, 17)


class TestBetween:
    def test_pause_overnight_counts_only_working_time(self):
        # The ADR example: 17:00 to 10:00 the next day is 2 business hours.
        assert business_minutes_between(madrid(23, 17), madrid(24, 10), OFFICE) == 120

    def test_weekend_counts_nothing(self):
        assert business_minutes_between(madrid(27, 18), madrid(30, 9), OFFICE) == 0

    def test_split_shift(self):
        calendar = schedule(weekdays=[Slot(time(9), time(14)), Slot(time(15), time(18))])

        assert business_minutes_between(madrid(23, 13), madrid(23, 16), calendar) == 120

    def test_holiday_counts_nothing(self):
        calendar = schedule(holidays=[date(2026, 12, 8)])

        minutes = business_minutes_between(
            madrid(7, 17, month=12), madrid(9, 10, month=12), calendar
        )

        assert minutes == 120

    def test_night_shift_until_midnight(self):
        calendar = schedule(
            weekdays=(),
            days={0: [Slot(time(22), time(0))], 1: [Slot(time(0), time(6))]},
        )

        assert business_minutes_between(madrid(23, 23), madrid(24, 2), calendar) == 180

    def test_negative_when_the_end_comes_first(self):
        assert business_minutes_between(madrid(24, 10), madrid(23, 17), OFFICE) == -120

    @pytest.mark.parametrize("seconds", [59, 61, 119])
    def test_partial_minutes_truncate_towards_zero(self, seconds):
        start = madrid(23, 10)
        end = start + timedelta(seconds=seconds)

        assert business_minutes_between(start, end, OFFICE) == seconds // 60
        assert business_minutes_between(end, start, OFFICE) == -(seconds // 60)

    def test_daylight_saving_change_counts_real_time(self):
        # 24/7 in Madrid on 29 March 2026: 00:00 to 04:00 local is 3 real hours.
        start = datetime(2026, 3, 29, 0, tzinfo=MADRID)
        end = datetime(2026, 3, 29, 4, tzinfo=MADRID)

        assert business_minutes_between(start, end, ALWAYS_OPEN) == 180


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 3, 29), 120),  # Spring: 02:00-03:00 does not exist.
        (date(2026, 10, 25), 240),  # Autumn: 02:00-03:00 happens twice.
    ],
)
def test_slot_across_a_daylight_saving_change_lasts_the_real_time(day, expected):
    # A whole slot inside the range: both of its ends are in the calendar's zone,
    # and subtracting datetimes with the same tzinfo would give 3 hours both times.
    calendar = schedule(weekdays=(), days={6: [Slot(time(1), time(4))]})  # Sundays.
    saturday = datetime.combine(day - timedelta(days=1), time(12), tzinfo=MADRID)
    monday = datetime.combine(day + timedelta(days=1), time(12), tzinfo=MADRID)

    assert business_minutes_between(saturday, monday, calendar) == expected
    assert add_business_minutes(saturday, expected, calendar) == datetime.combine(
        day, time(4), tzinfo=MADRID
    )


def test_add_and_between_are_inverse():
    end = add_business_minutes(madrid(27, 17, 30), 500, OFFICE)

    assert business_minutes_between(madrid(27, 17, 30), end, OFFICE) == 500


class TestImpossibleCalendars:
    def test_no_working_hours_cannot_add(self):
        with pytest.raises(ValueError, match="no tiene horario laboral"):
            add_business_minutes(madrid(23, 10), 60, schedule(weekdays=()))

    def test_no_working_hours_measures_zero(self):
        assert business_minutes_between(madrid(23, 10), madrid(30, 10), schedule(weekdays=())) == 0

    def test_search_gives_up(self, monkeypatch):
        monkeypatch.setattr(business_time, "MAX_SEARCH_DAYS", 10)
        # Mondays only, and the next two Mondays are holidays.
        calendar = schedule(
            weekdays=(),
            days={0: NINE_TO_SIX},
            holidays=[date(2026, 11, 30), date(2026, 12, 7)],
        )

        with pytest.raises(ValueError, match="10 días"):
            add_business_minutes(madrid(27, 10), 60, calendar)


def test_naive_datetimes_are_rejected():
    naive = datetime(2026, 11, 23, 10, 0)

    with pytest.raises(ValueError, match="aware"):
        add_business_minutes(naive, 10, OFFICE)
    with pytest.raises(ValueError, match="aware"):
        business_minutes_between(madrid(23, 10), naive, OFFICE)


def test_schedule_is_immutable():
    with pytest.raises(AttributeError):
        OFFICE.holidays = frozenset()
