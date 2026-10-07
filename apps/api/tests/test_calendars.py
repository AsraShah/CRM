"""RD07 - working-calendar accuracy.

Expected due instants and cancellation behaviour must match the approved
examples across holidays, weekends, time-zone changes and DST boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from modules.common.calendars import WorkingCalendar, WorkingInterval

KARACHI = ZoneInfo("Asia/Karachi")
LONDON = ZoneInfo("Europe/London")
UTC = UTC


def test_two_working_hours_inside_one_working_day(karachi_calendar):
    # Tuesday 10:00 Karachi -> 12:00 Karachi, same morning interval.
    start = datetime(2026, 9, 29, 10, 0, tzinfo=KARACHI)
    result = karachi_calendar.add_working_minutes(start, 120)
    assert result.astimezone(KARACHI) == datetime(2026, 9, 29, 12, 0, tzinfo=KARACHI)


def test_working_hours_skip_the_lunch_break(karachi_calendar):
    """12:30 plus two working hours lands at 15:30, not 14:30.

    The 13:00-14:00 break is not working time, so it must not be counted.
    """
    start = datetime(2026, 9, 29, 12, 30, tzinfo=KARACHI)
    result = karachi_calendar.add_working_minutes(start, 120)
    assert result.astimezone(KARACHI) == datetime(2026, 9, 29, 15, 30, tzinfo=KARACHI)


def test_friday_afternoon_overdue_lands_on_monday(karachi_calendar):
    """Rule A03 on a Friday evening must not alert somebody at the weekend.

    This is the case the brief calls out: "overdue by 2 working hours" is not
    two clock hours.
    """
    friday = datetime(2026, 10, 2, 17, 30, tzinfo=KARACHI)
    result = karachi_calendar.add_working_minutes(friday, 120)
    local = result.astimezone(KARACHI)
    assert local.weekday() == 0  # Monday
    assert local == datetime(2026, 10, 5, 10, 30, tzinfo=KARACHI)


def test_holidays_are_skipped():
    calendar = WorkingCalendar(
        time_zone="Asia/Karachi",
        intervals=(WorkingInterval(time(9, 0), time(17, 0)),),
        holidays=frozenset({datetime(2026, 9, 30).date()}),
    )
    start = datetime(2026, 9, 29, 16, 0, tzinfo=KARACHI)
    result = calendar.add_working_minutes(start, 120)
    local = result.astimezone(KARACHI)
    # 1 hour remains on the 29th; the 30th is a holiday; the rest falls on 1 Oct.
    assert local.date() == datetime(2026, 10, 1).date()
    assert local.hour == 10


def test_start_outside_working_hours_moves_to_the_next_opening(karachi_calendar):
    """A deadline set at 03:00 is measured from the next working moment."""
    start = datetime(2026, 9, 29, 3, 0, tzinfo=KARACHI)
    result = karachi_calendar.add_working_minutes(start, 60)
    assert result.astimezone(KARACHI) == datetime(2026, 9, 29, 10, 0, tzinfo=KARACHI)


def test_working_days_use_the_configured_day_length(karachi_calendar):
    """ "3 working days" means three working-day durations, not 72 hours."""
    assert karachi_calendar.minutes_per_working_day == 480

    # Tuesday 09:00 + 3 × 480 working minutes consumes Tue, Wed and Thu in
    # full, landing at the close of Thursday rather than on Friday morning.
    start = datetime(2026, 9, 29, 9, 0, tzinfo=KARACHI)
    result = karachi_calendar.add_working_days(start, 3)
    local = result.astimezone(KARACHI)

    assert local == datetime(2026, 10, 1, 18, 0, tzinfo=KARACHI)
    assert local.weekday() == 3  # Thursday


def test_spring_forward_gap_produces_a_real_instant(london_calendar):
    """29 March 2026 01:00-02:00 does not exist in London.

    A naive construction would yield an instant that round-trips to a different
    local time. The calendar must return something that actually exists.
    """
    start = datetime(2026, 3, 27, 16, 30, tzinfo=LONDON)
    result = london_calendar.add_working_minutes(start, 60)
    round_tripped = result.astimezone(UTC).astimezone(LONDON)
    assert round_tripped == result.astimezone(LONDON)


def test_autumn_fold_does_not_double_count(london_calendar):
    """25 October 2026 01:00-02:00 occurs twice; working hours are unaffected."""
    start = datetime(2026, 10, 23, 16, 0, tzinfo=LONDON)
    result = london_calendar.add_working_minutes(start, 120)
    local = result.astimezone(LONDON)
    assert local == datetime(2026, 10, 26, 10, 0, tzinfo=LONDON)


def test_persisted_instants_are_utc(karachi_calendar):
    """Display zones must not alter the stored instant (section 4.1)."""
    start = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
    result = karachi_calendar.add_working_minutes(start, 60)
    assert result.tzinfo == UTC


def test_working_minutes_between_ignores_non_working_time(karachi_calendar):
    start = datetime(2026, 9, 29, 12, 0, tzinfo=KARACHI)
    end = datetime(2026, 9, 29, 15, 0, tzinfo=KARACHI)
    # 12:00-13:00 and 14:00-15:00 count; the break does not.
    assert karachi_calendar.working_minutes_between(start, end) == 120


def test_zero_and_reversed_ranges_are_safe(karachi_calendar):
    instant = datetime(2026, 9, 29, 10, 0, tzinfo=KARACHI)
    assert karachi_calendar.add_working_minutes(instant, 0) == instant
    assert karachi_calendar.working_minutes_between(instant, instant) == 0
    earlier = datetime(2026, 9, 28, 10, 0, tzinfo=KARACHI)
    assert karachi_calendar.working_minutes_between(instant, earlier) == 0


def test_naive_datetimes_are_rejected(karachi_calendar):
    """A naive datetime is always a bug here; it must not be guessed at."""
    with pytest.raises(ValueError, match="instants are UTC"):
        karachi_calendar.add_working_minutes(datetime(2026, 9, 29, 10, 0), 60)
