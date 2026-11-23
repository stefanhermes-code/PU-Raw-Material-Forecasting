"""ECB rates, looked up on the date a price was struck.

One copy of this, because two would drift. `app/domain/comparable.py` reads it to put series side by
side, and the loaders read it to store a series with its FX effect already taken out.

Rates are stored per date like any other evidence. A day the ECB did not publish - a weekend, a
holiday - takes the most recent published rate before it rather than an interpolated one: a rate is
something that was quoted on a date, and inventing one between two quotes invents evidence.
"""
from __future__ import annotations

import weakref
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models import FxRateModel

# Long enough for any run of public holidays, short enough that a rate table which has stopped
# updating fails loudly instead of quietly reusing a stale number for ever.
RATE_LOOKBACK_DAYS = 10

# A price struck before the first fixing we hold for its currency takes that first fixing, if it is
# within a few days.
#
# This is not a lookback by another name and it is not interpolation. It is the one case where
# "the last quote before this day" has no answer at all, because the quote does not exist yet:
# the euro began on 1 January 1999 and the ECB's first reference rate is 4 January 1999, the
# first business day. Sixteen Comext customs values are stamped 1999-01-01 and so could not be
# shown in dollars at all - not because the rate is unknown, but because it is three days late.
#
# `raw_material_forecasting.fx.rate_on` has always done this, with the same tolerance. The two
# were meant to be one rule and had drifted, which is what the sixteen were the symptom of.
FORWARD_TO_FIRST_FIXING_DAYS = 7


# The table as last read, with the stamp it was read at. See `rate_table`.
#
# Keyed on the engine itself, not on its URL: a test suite builds dozens of in-memory databases and
# every one of them is `sqlite://`, so a URL key would hand one test's rates to the next. Weak, so
# an entry goes when the engine it belongs to does.
_LAST_READ: "weakref.WeakKeyDictionary[Any, tuple[tuple, dict[date, dict[str, float]]]]" = \
    weakref.WeakKeyDictionary()


def _stamp(session) -> tuple:
    """Cheap proof of whether the stored rates have changed since they were last read.

    How many rows there are and the newest `fetched_at`. Two aggregates, one round trip, a few
    bytes back - against 35,408 rows. Every write to this table comes from a loader that stamps
    `fetched_at` (`app/fx/ecb.py`), so an insert moves the count and a re-fetch moves the maximum.
    """
    return tuple(session.execute(
        select(func.count(FxRateModel.id), func.max(FxRateModel.fetched_at))).one())


def rate_table(session) -> dict[date, dict[str, float]]:
    """Every stored rate, keyed by date then currency. EUR base, as the ECB publishes.

    READ ONCE PER CHANGE, NOT ONCE PER CALLER. Six call sites ask for this and they ask per run,
    per market and per reading: measured 5 November 2026, 16,395 calls had returned 580,296,980
    rows - about 60 GB of Supabase egress on a table that is 35,408 rows and changes once a day.
    The rates themselves are nothing; asking for all of them sixteen thousand times is the cost.

    So the rows come back only when `_stamp` says the table is not the one already in hand. The
    dict is handed out as-is rather than copied - 35,408 dates deep-copied per call would put back
    the cost this removes - so **callers must not mutate it**. None do: `cross_rate`, `_on` and
    `_first_quoted` read, and the five modules that pass the table around only read it.

    Three columns, not seven. `id`, `base` and `source` are not looked at here, and on a database
    in Singapore read from a PC in Bangkok every column is paid for.
    """
    stamp = _stamp(session)
    where = session.get_bind()
    held = _LAST_READ.get(where)
    if held is not None and held[0] == stamp:
        return held[1]

    table: dict[date, dict[str, float]] = {}
    for rate_date, currency, rate in session.execute(
            select(FxRateModel.rate_date, FxRateModel.currency, FxRateModel.rate)):
        when = rate_date.date() if isinstance(rate_date, datetime) else rate_date
        table.setdefault(when, {})[currency.upper()] = float(rate)
    _LAST_READ[where] = (stamp, table)
    return table


def cross_rate(table: dict[date, dict[str, float]], when: date,
               frm: str, to: str) -> tuple[float, date] | None:
    """Units of `to` per unit of `frm` on `when`, with the date the rate was actually quoted."""
    frm, to = (frm or "").upper(), (to or "").upper()
    if not frm or not to:
        return None
    if frm == to:
        return 1.0, when
    day = when
    for _ in range(RATE_LOOKBACK_DAYS + 1):
        rate = _on(table, day, frm, to)
        if rate is not None:
            return rate, day
        day -= timedelta(days=1)

    # Nothing before it. The first fixing this pair ever had, if the price is within reach of it.
    first = _first_quoted(table, frm, to)
    if first is not None and 0 < (first - when).days <= FORWARD_TO_FIRST_FIXING_DAYS:
        rate = _on(table, first, frm, to)
        if rate is not None:
            return rate, first
    return None


def _on(table: dict[date, dict[str, float]], day: date, frm: str, to: str) -> float | None:
    """The cross rate quoted on exactly this day, or None if this day quoted neither side."""
    quoted = table.get(day)
    if not quoted:
        return None
    left = 1.0 if frm == "EUR" else quoted.get(frm)
    right = 1.0 if to == "EUR" else quoted.get(to)
    return (right / left) if left and right else None


def _first_quoted(table: dict[date, dict[str, float]], frm: str, to: str) -> date | None:
    """The earliest day this pair can be crossed on."""
    days = [day for day in table if _on(table, day, frm, to) is not None]
    return min(days) if days else None
