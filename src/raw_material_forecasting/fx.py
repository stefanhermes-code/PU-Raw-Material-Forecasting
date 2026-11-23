"""Display-currency conversion for market evidence.

The engine stores every observation in the currency the publisher used; that is the
governed evidence and is never changed. This module converts a *copy* of the snapshot
for display, using the ECB reference rate valid on each observation's own date (last
fixing on or before the date). Converted rows keep the original value in
``source_price`` / ``source_currency`` so the evidence stays traceable.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# Stefan, 27 October 2026: "Drop THB. Only USD, EUR and CNY." THB remains a currency a reading
# can be PUBLISHED in - KGI's Thai purchases are paid in baht - and the ECB rate for it is still
# fetched so those readings can be converted. It is no longer one a reader can choose.
DISPLAY_CURRENCIES: tuple[str, ...] = ("USD", "CNY", "EUR")
DEFAULT_DISPLAY_CURRENCY = "USD"
# How far before the first stored fixing a price may still be converted at that fixing.
BACKWARD_TOLERANCE_DAYS = 7
# A fixing just after the day, where the one before it is far older.
FORWARD_TOLERANCE_DAYS = 2
_ALIASES = {"RMB": "CNY"}


def _norm(currency: Any) -> str:
    code = str(currency or "").strip().upper()
    return _ALIASES.get(code, code)


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# Public names for the two helpers other modules legitimately need: the correlation engine has to
# parse observation dates and normalise currency codes exactly the way conversion does, and a second
# implementation of either would eventually disagree with this one.
as_date = _as_date
normalise_currency = _norm


@dataclass
class FxTable:
    """EUR-based daily rates: rates[currency] = sorted list of (date, units of currency per 1 EUR)."""

    base: str = "EUR"
    rates: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    latest_date: date | None = None

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, Any]], base: str = "EUR") -> "FxTable":
        table = cls(base=base)
        for row in rows:
            day = _as_date(row.get("date"))
            code = _norm(row.get("currency"))
            if day is None or not code:
                continue
            table.rates.setdefault(code, []).append((day, float(row["rate"])))
        for code in table.rates:
            table.rates[code].sort()
            last = table.rates[code][-1][0]
            table.latest_date = last if table.latest_date is None or last > table.latest_date else table.latest_date
        return table

    @property
    def available(self) -> bool:
        return bool(self.rates)

    def currencies(self) -> list[str]:
        return sorted(set(self.rates) | {self.base})

    def rate_on(self, currency: str, on: date | None) -> tuple[float, date | None] | None:
        """Units of `currency` per 1 base unit, valid on `on`: the last fixing on or before it.

        A date earlier than the whole stored history returns None rather than the earliest fixing.
        Falling back would convert a 2014 price at a 2023 rate and report it as converted - on the
        CNY history that is a double-digit error applied silently to every month of it. Refusing is
        the only honest answer; the fix is to backfill the rates, not to guess them. A few days of
        slack is allowed so a price printed just before the first stored fixing still converts.
        """
        code = _norm(currency)
        if code == self.base:
            return 1.0, on
        series = self.rates.get(code)
        if not series:
            return None
        if on is None:
            return series[-1][1], series[-1][0]
        index = bisect_right([d for d, _ in series], on) - 1
        if index < 0:
            earliest_date, earliest_rate = series[0]
            if (earliest_date - on).days > BACKWARD_TOLERANCE_DAYS:
                return None
            return earliest_rate, earliest_date
        found_date, found_rate = series[index][0], series[index][1]
        if (on - found_date).days > BACKWARD_TOLERANCE_DAYS and index + 1 < len(series):
            # The last fixing before this day is weeks old. A fixing a day or two after it is
            # the nearer reading of the same market.
            next_date, next_rate = series[index + 1]
            if (next_date - on).days <= FORWARD_TOLERANCE_DAYS:
                return next_rate, next_date
        return found_rate, found_date

    def convert(self, amount: float, from_currency: str, to_currency: str, on: date | None) -> tuple[float, float, date | None] | None:
        """Returns (converted_amount, effective rate from->to, rate date) or None if a rate is missing."""
        src, dst = _norm(from_currency), _norm(to_currency)
        if src == dst:
            return float(amount), 1.0, on
        src_rate = self.rate_on(src, on)
        dst_rate = self.rate_on(dst, on)
        if src_rate is None or dst_rate is None or src_rate[0] == 0:
            return None
        cross = dst_rate[0] / src_rate[0]
        used_date = dst_rate[1] if dst != self.base else src_rate[1]
        return float(amount) * cross, cross, used_date


def convert_row(row: dict[str, Any], table: FxTable, target: str, *, value_key: str, date_keys: tuple[str, ...]) -> dict[str, Any]:
    out = dict(row)
    value = row.get(value_key)
    currency = _norm(row.get("currency"))
    target = _norm(target)
    if value is None or not currency or currency == target:
        if currency == target and "source_currency" not in out:
            out["source_currency"] = currency
        return out
    on = None
    for key in date_keys:
        on = _as_date(row.get(key))
        if on is not None:
            break
    result = table.convert(float(value), currency, target, on)
    if result is None:
        out["fx_unconverted"] = True
        return out
    converted, cross, rate_date = result
    out[value_key] = converted
    out["source_price"] = float(value)
    out["source_currency"] = currency
    out["currency"] = target
    out["fx_rate"] = cross
    out["fx_rate_date"] = rate_date.isoformat() if rate_date else None
    return out


def already_in(row: dict[str, Any], target: str) -> bool:
    """Whether the engine already served this reading in the currency the reader asked for.

    Since v0.4.263 a reading is converted when the datapoint is created and the engine serves the
    stored value, so the page has nothing to compute. A reading it could not convert comes back
    with a null price and `fx_unconverted`, and converting that here would put the number it is
    assessed in back on an axis it does not belong to - which is the defect this replaced.
    """
    return bool(row.get("fx_unconverted")) or _norm(row.get("currency")) == _norm(target)


def convert_snapshot(snapshot, table: FxTable, target: str):
    """A deep copy of the engine snapshot with prices/references in `target` currency.

    Readings the engine already served in the target currency pass straight through. Older engines
    serve them in the currency each market is assessed in, and those are converted here as before,
    so the page still works against one.
    """
    target = _norm(target)
    converted = deepcopy(snapshot)
    converted.observations = [
        obs if already_in(obs, target) else
        convert_row(obs, table, target, value_key="price", date_keys=("observation_date", "publication_date", "available_from"))
        for obs in snapshot.observations
    ]
    converted.references = [
        convert_row(ref, table, target, value_key="reference_value", date_keys=("reference_date",))
        for ref in snapshot.references
    ]
    converted.display_currency = target
    converted.fx_latest_date = table.latest_date.isoformat() if table.latest_date else None
    converted.fx_unconverted = sum(1 for row in converted.observations if row.get("fx_unconverted"))
    return converted


def source_currency(row: dict[str, Any]) -> str:
    """The currency the publisher used, whether or not the row has been converted for display."""
    return _norm(row.get("source_currency") or row.get("currency"))
