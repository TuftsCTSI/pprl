"""Keep every check that pprl makes on a date in one place.

- A date that a project hashes must be a real date written yyyy-mm-dd
  (``is_date``). Clean repairs dates before it checks them.
- Times in deployment.yml are written yyyy-mm-dd HH:MM (``as_datetime``).
- Merge's ``take_latest`` and ``take_earliest`` rules compare ISO 8601 dates
  or datetimes. pandas reads them one column at a time (``as_iso_8601`` and
  ``are_iso_8601``).

This module imports nothing from pprl, so any module can use it.
"""

import re
from datetime import date, datetime

import pandas as pd

# A regular expression for a date written yyyy-mm-dd. Clean also uses it to
# find a date that has a time after it.
DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"


def as_datetime(value: object) -> datetime | None:
    """Read a date and time written yyyy-mm-dd HH:MM (or H:MM) as a datetime.

    The time must use a 24-hour clock, and the date and time must exist.
    Return None for anything else.

    >>> as_datetime("2026-11-16 09:00"), as_datetime("2026-11-16 9:30")
    (datetime.datetime(2026, 11, 16, 9, 0), datetime.datetime(2026, 11, 16, 9, 30))
    >>> as_datetime("2026-02-30 09:00"), as_datetime("2026-11-16 24:00")
    (None, None)
    >>> as_datetime("2026-11-16"), as_datetime("09:00"), as_datetime(540)
    (None, None, None)
    """
    if not isinstance(value, str):
        return None
    if not re.fullmatch(DATE + r" [0-9]{1,2}:[0-9]{2}", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")  # noqa: DTZ007 (local time)
    except ValueError:
        return None


def is_date(value: object) -> bool:
    """Tell whether a value is a real date written yyyy-mm-dd.

    >>> [is_date(v) for v in ("1990-03-04", "1990-02-30", "03/04/1990")]
    [True, False, False]
    """
    if not isinstance(value, str) or not re.fullmatch(DATE, value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def as_iso_8601(series: pd.Series) -> pd.Series:
    """Convert ISO 8601 dates or datetimes to pandas timestamps.

    Values must be yyyy-mm-dd or yyyy-mm-dd HH:MM:SS. Raise ValueError if any
    value is not.
    """
    return pd.to_datetime(series, format="ISO8601")


def are_iso_8601(values: pd.Series) -> pd.Series:
    """Tell which values are ISO 8601 dates or datetimes.

    An empty value does not count. For speed, all values are parsed together
    first. Values are checked one at a time only where pandas cannot parse
    them together, such as datetimes in different time zones.
    """
    try:
        parsed = pd.to_datetime(values, format="ISO8601", errors="coerce")
        return parsed.notna() if len(values) else values.astype(bool)
    except ValueError, TypeError:
        return values.map(lambda v: isinstance(v, str) and _is_iso_8601(v))


def _is_iso_8601(value: str) -> bool:
    """Tell whether a value is an ISO 8601 date or datetime.

    >>> [_is_iso_8601(v) for v in ("2024-03-01", "2024-03-01 09:30:00", "03/01/2024")]
    [True, True, False]
    """
    try:
        as_iso_8601(pd.Series([value]))
    except ValueError:
        return False
    return True
