"""Working-calendar arithmetic (SVX-TECH-001 section 8.3, RD07).

Every automatic deadline in the rule catalogue is expressed in *working*
minutes, hours or days -- "overdue by 2 working hours", "no activity for 3
working days". Those are not wall-clock durations, so they cannot be computed
with a timedelta.

Deadlines are always calculated on the server, and both the resulting instant
and the calendar version used are persisted. Recomputing later from a changed
calendar would silently move commitments that people have already been held to.

Instants are UTC throughout. The workspace IANA zone is used only to decide
which local day and local time-of-day an instant falls in. ScaleVexo starts on
Asia/Karachi, which has no DST, but the gap and fold cases are still handled and
tested -- the first customer in a DST zone must not be the thing that discovers
the bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MINUTES_PER_HOUR = 60


@dataclass(frozen=True, slots=True)
class WorkingInterval:
    """A local-time window on a working day, e.g. 09:00-17:00."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("A working interval must start before it ends.")

    @property
    def minutes(self) -> int:
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)


@dataclass(frozen=True, slots=True)
class WorkingCalendar:
    """A workspace's working hours, weekdays and holidays.

    ``version`` is stored next to every deadline the calendar produced, so a
    later edit is traceable and never retroactively reinterprets an existing
    commitment.
    """

    time_zone: str
    version: int = 1
    # Monday = 0 ... Sunday = 6. ScaleVexo's default working week is Mon-Fri.
    working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    intervals: tuple[WorkingInterval, ...] = (
        WorkingInterval(time(9, 0), time(13, 0)),
        WorkingInterval(time(14, 0), time(18, 0)),
    )
    holidays: frozenset[date] = field(default_factory=frozenset)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    @property
    def minutes_per_working_day(self) -> int:
        return sum(interval.minutes for interval in self.intervals)

    def is_working_day(self, day: date) -> bool:
        return day.weekday() in self.working_weekdays and day not in self.holidays

    def _local(self, instant: datetime) -> datetime:
        if instant.tzinfo is None:
            raise ValueError("Naive datetimes are never accepted; instants are UTC.")
        return instant.astimezone(self.zone)

    def _at(self, day: date, moment: time) -> datetime:
        """Build a local instant, resolving DST gaps and folds explicitly.

        On a spring-forward day the requested local time may not exist. Python
        would hand back a plausible-looking but wrong instant, so we step
        forward in one-minute increments until the round-trip through UTC agrees
        with what we asked for.
        """
        candidate = datetime.combine(day, moment, tzinfo=self.zone)
        round_tripped = candidate.astimezone(ZoneInfo("UTC")).astimezone(self.zone)
        if round_tripped.timetz() != candidate.timetz():
            probe = candidate
            for _ in range(180):
                probe += timedelta(minutes=1)
                if probe.astimezone(ZoneInfo("UTC")).astimezone(self.zone) == probe:
                    return probe
        return candidate

    def is_working_time(self, instant: datetime) -> bool:
        local = self._local(instant)
        if not self.is_working_day(local.date()):
            return False
        return any(
            interval.start <= local.timetz().replace(tzinfo=None) < interval.end
            for interval in self.intervals
        )

    def next_working_instant(self, instant: datetime) -> datetime:
        """Move forward to the first moment that is inside working hours."""
        local = self._local(instant)
        for _ in range(400):  # ~1 year of consecutive non-working days
            if self.is_working_day(local.date()):
                naive = local.replace(tzinfo=None).time()
                for interval in self.intervals:
                    if naive < interval.start:
                        return self._at(local.date(), interval.start).astimezone(instant.tzinfo)
                    if interval.start <= naive < interval.end:
                        return instant
            local = datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=self.zone)
        raise ValueError("No working time found within a year. Check the calendar configuration.")

    def add_working_minutes(self, instant: datetime, minutes: int) -> datetime:
        """Return the instant `minutes` of working time after `instant`.

        Non-working evenings, weekends and holidays are skipped rather than
        counted, so "overdue by 2 working hours" on a Friday afternoon lands on
        Monday morning, not Friday night.
        """
        if minutes < 0:
            raise ValueError("Use subtract_working_minutes for negative durations.")
        if minutes == 0:
            return instant

        cursor = self.next_working_instant(instant)
        remaining = minutes

        for _ in range(4000):
            local = self._local(cursor)
            naive = local.replace(tzinfo=None).time()
            interval = next((i for i in self.intervals if i.start <= naive < i.end), None)
            if interval is None:
                cursor = self.next_working_instant(cursor + timedelta(minutes=1))
                continue

            interval_end = self._at(local.date(), interval.end)
            available = int((interval_end - local).total_seconds() // 60)
            if remaining <= available:
                return (local + timedelta(minutes=remaining)).astimezone(instant.tzinfo)

            remaining -= available
            cursor = self.next_working_instant(interval_end + timedelta(minutes=1))

        raise ValueError("Working-minute calculation did not converge; check intervals.")

    def add_working_hours(self, instant: datetime, hours: int) -> datetime:
        return self.add_working_minutes(instant, hours * MINUTES_PER_HOUR)

    def add_working_days(self, instant: datetime, days: int) -> datetime:
        """Add whole working days, measured as full working-day durations."""
        return self.add_working_minutes(instant, days * self.minutes_per_working_day)

    def working_minutes_between(self, start: datetime, end: datetime) -> int:
        """Count working minutes elapsed, used for stage age and overdue checks."""
        if end <= start:
            return 0
        total = 0
        cursor = start
        for _ in range(4000):
            cursor = self.next_working_instant(cursor)
            if cursor >= end:
                return total
            local = self._local(cursor)
            naive = local.replace(tzinfo=None).time()
            interval = next((i for i in self.intervals if i.start <= naive < i.end), None)
            if interval is None:
                cursor += timedelta(minutes=1)
                continue
            interval_end = self._at(local.date(), interval.end)
            segment_end = min(interval_end, end)
            total += int((segment_end - local).total_seconds() // 60)
            cursor = interval_end + timedelta(minutes=1)
        raise ValueError("Working-minute count did not converge; check intervals.")
