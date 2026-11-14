"""Cron expressions: when a schedule is due, in its time zone.

Five fields: minute (0-59), hour (0-23), day of month (1-31), month (1-12), day of week (0-6,
Sunday is 0 or 7). Each is `*`, a number, a range `a-b`, a step `*/n` or `a-b/n`, or a list of
these. When both day fields are restricted, a day matching either counts (as cron does).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from p42cloud.data.types import Invalid

_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 7))
_HORIZON_DAYS = 5 * 366  # Feb 29 on a Monday can take years; never longer than this
DECEMBER = 12


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    def _day_matches(self, moment: datetime) -> bool:
        weekday = (moment.weekday() + 1) % 7  # Python: Monday 0; cron: Sunday 0
        on_day = moment.day in self.days
        on_weekday = weekday in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return on_weekday
        if self.any_weekday:
            return on_day
        return on_day or on_weekday

    def next_after(self, after: datetime, zone: str = "UTC") -> datetime:
        """The first moment after `after` (aware) that matches, in UTC."""
        tz = ZoneInfo(zone)
        moment = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = moment + timedelta(days=_HORIZON_DAYS)
        while moment < limit:
            if moment.month not in self.months:
                ahead = moment.month == DECEMBER
                year = moment.year + 1 if ahead else moment.year
                month = 1 if ahead else moment.month + 1
                moment = moment.replace(year=year, month=month, day=1, hour=0, minute=0)
            elif not self._day_matches(moment):
                moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            elif moment.hour not in self.hours:
                moment = (moment + timedelta(hours=1)).replace(minute=0)
            elif moment.minute not in self.minutes:
                moment += timedelta(minutes=1)
            else:
                return moment.astimezone(UTC)
        raise Invalid("This schedule never comes round.")


def _part(text: str, low: int, high: int) -> set[int]:
    step = 1
    if "/" in text:
        text, given = text.split("/", 1)
        if not given.isdigit() or int(given) < 1:
            raise Invalid(f"Not a step: {given!r}.")
        step = int(given)
    if text == "*":
        start, end = low, high
    elif "-" in text:
        first, last = text.split("-", 1)
        if not (first.isdigit() and last.isdigit()):
            raise Invalid(f"Not a range: {text!r}.")
        start, end = int(first), int(last)
    elif text.isdigit():
        start = int(text)
        end = high if step > 1 else start
    else:
        raise Invalid(f"Not a number: {text!r}.")
    if not low <= start <= end <= high:
        raise Invalid(f"{text!r} is outside {low} to {high}.")
    return set(range(start, end + 1, step))


def parse(expression: str) -> Cron:
    parts = expression.split()
    if len(parts) != len(_FIELDS):
        raise Invalid("Five fields: minute, hour, day of month, month, day of week.")
    found: list[set[int]] = []
    for text, (name, low, high) in zip(parts, _FIELDS, strict=True):
        values: set[int] = set()
        for piece in text.split(","):
            try:
                values |= _part(piece, low, high)
            except Invalid as error:
                raise Invalid(f"{name}: {error}") from error
        found.append(values)
    minutes, hours, days, months, weekdays = found
    weekdays = {0 if d == 7 else d for d in weekdays}  # noqa: PLR2004 - Sunday twice
    return Cron(
        frozenset(minutes),
        frozenset(hours),
        frozenset(days),
        frozenset(months),
        frozenset(weekdays),
        any_day=parts[2] == "*",
        any_weekday=parts[4] == "*",
    )
