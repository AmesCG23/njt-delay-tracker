"""
federal_holidays.py — US Federal Holiday Calendar
-----------------------------------------------------
Used by daily.py to skip the pipeline entirely on days when "yesterday"
was a federal holiday. NJ Transit runs a holiday (reduced/Sunday-type)
schedule on these days, not the normal weekday rush-hour service this
tracker's methodology assumes — so there's nothing meaningful to measure
or post about.

No third-party dependency: the 11 federal holidays are fixed by simple,
well-known rules (a specific weekday-of-month, or a fixed calendar date
with the standard Saturday→Friday / Sunday→Monday "observed" shift), so
they're computed directly rather than pulled from a library. This also
keeps the whole calendar auditable in one place.

Deliberately named "federal_holidays" rather than "holidays" so it can
never be shadowed by (or shadow) the third-party `holidays` PyPI package
if that's ever installed alongside this project.
"""

from datetime import date, timedelta


def _nth_weekday(year, month, weekday, n):
    """The date of the nth occurrence of `weekday` (Mon=0..Sun=6) in a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    """The date of the last occurrence of `weekday` (Mon=0..Sun=6) in a month."""
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _raw_federal_holidays(year):
    """
    The 11 US federal holidays for a given year, before the Saturday/Sunday
    "observed" shift is applied.
    """
    return {
        "New Year's Day":                          date(year, 1, 1),
        "Martin Luther King Jr. Day":               _nth_weekday(year, 1, 0, 3),
        "Washington's Birthday (Presidents Day)":   _nth_weekday(year, 2, 0, 3),
        "Memorial Day":                             _last_weekday(year, 5, 0),
        "Juneteenth":                               date(year, 6, 19),
        "Independence Day":                         date(year, 7, 4),
        "Labor Day":                                _nth_weekday(year, 9, 0, 1),
        "Columbus Day":                              _nth_weekday(year, 10, 0, 2),
        "Veterans Day":                             date(year, 11, 11),
        "Thanksgiving Day":                         _nth_weekday(year, 11, 3, 4),
        "Christmas Day":                            date(year, 12, 25),
    }


def _observed(d):
    """
    Apply the standard federal "observed" shift: a holiday landing on
    Saturday is observed the preceding Friday; on Sunday, the following
    Monday. Weekday-rule holidays (MLK Day, Labor Day, etc.) never land
    on a weekend, so this only affects the fixed-date holidays.
    """
    if d.weekday() == 5:      # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:      # Sunday
        return d + timedelta(days=1)
    return d


def federal_holiday_name(d):
    """
    Return the holiday name if `d` is an observed US federal holiday,
    else None.

    Checks the surrounding three years' raw holiday dates (not just d's
    own year) so that year-boundary shifts are caught correctly — e.g.
    if January 1 falls on a Saturday, it's observed on December 31 of
    the *previous* year.
    """
    for year in (d.year - 1, d.year, d.year + 1):
        for name, raw_date in _raw_federal_holidays(year).items():
            if _observed(raw_date) == d:
                return name
    return None
