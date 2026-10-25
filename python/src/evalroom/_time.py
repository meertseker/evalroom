"""Timestamp parsing and formatting compatible with JavaScript's ``Date``."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

__all__ = ["now_iso", "parse_time"]

_ISO = re.compile(
    r"^(?P<date>[+-]?\d{4,6}-\d{2}-\d{2})"
    r"(?:[T ](?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)"
    r"(?P<tz>Z|z|[+-]\d{2}:?\d{2})?)?$"
)


def parse_time(text: str) -> float | None:
    """Milliseconds since the epoch for an ISO-8601 timestamp, or ``None`` if unparseable.

    Accepts what ``Date.parse`` accepts for ISO strings: a date alone (read as UTC), a date
    and time with an optional fractional part, and ``Z`` or an offset. A date-time without an
    offset is read as UTC (JavaScript would use local time; evalroom files always carry one).
    """
    match = _ISO.match(text.strip())
    if not match:
        return None
    try:
        day = date.fromisoformat(match.group("date"))
    except ValueError:
        return None
    clock = match.group("time")
    if clock is None:
        moment = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return moment.timestamp() * 1000
    hms, _, frac = clock.partition(".")
    parts = [int(x) for x in hms.split(":")]
    hour, minute = parts[0], parts[1]
    second = parts[2] if len(parts) > 2 else 0
    micro = int((frac + "000000")[:6]) if frac else 0
    tz_text = match.group("tz")
    tz = timezone.utc
    if tz_text and tz_text not in ("Z", "z"):
        sign = -1 if tz_text[0] == "-" else 1
        digits = tz_text[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        moment = datetime(day.year, day.month, day.day, hour, minute, second, micro, tzinfo=tz)
    except ValueError:
        return None
    return moment.timestamp() * 1000


def now_iso() -> str:
    """The current time as ``new Date().toISOString()`` prints it: ``2026-10-25T17:13:50.353Z``."""
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
