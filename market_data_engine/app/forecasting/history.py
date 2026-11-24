"""Cutoff-safe history: the series as it could have been read on a given day (plan, Task 4).

Two dates travel with every stored observation. `observation_date` is the day a price refers to;
`available_from` is the day it could first have been read. A customs month refers to March and is
readable in June. A backtest standing on 15 April must not see it, and a method asked on 15 April
must not be handed a March bucket that was still blank. This module is where that is enforced for
the tournament: `build_history(rows, cutoff)` keeps a row only when both dates are on or before the
cutoff, buckets what is left at the working frequency, and fills internal gaps only between two
periods both known at that cutoff.

The leakage this prevents is specific (plan, Task 4, step 1): a gap at an earlier origin must never
be filled from a later right endpoint. Standing on the cutoff, the right endpoint of a trailing gap
does not exist yet, so nothing can be interpolated into it; the latest expected period is reported
missing instead, and a method asked at that origin is skipped cleanly (Review Focus 1).

Buckets use `service.bucket` - the engine's own median-per-period - so the tournament fits on
exactly what the live forecast fits on.

Since v1.2.116 the newest period is the one exception: it is counted at the newest price the
cutoff allows rather than at the median of its prints so far. The truth a forecast is scored
against (`truth_at`) is still the whole period's median.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from app.forecasting.adapter import DAILY, WEEKLY, Series
from raw_material_forecasting.units import MT
from app.forecasting.service import (
    _as_date, _period, best_frequency, bucket, ending_on_the_newest_price)


@dataclass(frozen=True)
class HistoricalSeries:
    """A target series as it stood at a cutoff, with an account of what was done to it."""

    series: Series
    cutoff: date
    frequency: str
    expected_periods: int               # calendar periods from first to last expected, inclusive
    observed_periods: int               # periods with at least one visible print
    interpolated: tuple[date, ...]      # internal gaps filled, between two visible periods
    missing: tuple[date, ...]           # expected periods still blank after interpolation
    latest_missing: bool                # the period the cutoff falls in has no visible print
    rows_hidden: int                    # rows the cutoff hid (observed or available after it)

    @property
    def missing_share(self) -> float:
        return (len(self.missing) + len(self.interpolated)) / self.expected_periods if self.expected_periods else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff.isoformat(), "frequency": self.frequency,
            "expected_periods": self.expected_periods, "observed_periods": self.observed_periods,
            "interpolated": [d.isoformat() for d in self.interpolated],
            "missing": [d.isoformat() for d in self.missing],
            "missing_share": round(self.missing_share, 4), "latest_missing": self.latest_missing,
            "rows_hidden": self.rows_hidden, "points": len(self.series.points),
            "last_period": self.series.last_date.isoformat() if self.series.last_date else None,
        }


def _next_period(day: date, frequency: str) -> date:
    if frequency == DAILY:
        return day + timedelta(days=1)
    if frequency == WEEKLY:
        return day + timedelta(days=7)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, 1)


def period_end(day: date, frequency: str) -> date:
    """The last day of the period `day` starts."""
    return _next_period(_period(day, frequency), frequency) - timedelta(days=1)


def expected_periods(first: date, last: date, frequency: str) -> list[date]:
    """Every period start from `first` to `last` inclusive, at this frequency."""
    out = []
    day = _period(first, frequency)
    stop = _period(last, frequency)
    while day <= stop:
        out.append(day)
        day = _next_period(day, frequency)
    return out


def visible_rows(rows: Iterable[dict[str, Any]], cutoff: date) -> tuple[list[dict[str, Any]], int]:
    """Rows a reader standing on `cutoff` could have had. Returns (kept, hidden)."""
    kept, hidden = [], 0
    for row in rows:
        observed = _as_date(row.get("observation_date"))
        if observed is None or row.get("native_price", row.get("price")) is None:
            continue
        available = _as_date(row.get("available_from")) or _as_date(row.get("publication_date")) or observed
        if observed <= cutoff and available <= cutoff:
            kept.append(row)
        else:
            hidden += 1
    # Revisions (v0.5.60, gate finding 6): rows that carry a `version` are versions of one dated
    # value. Keep, per date, only the newest version this reader could have had.
    if any("version" in row for row in kept):
        newest: dict[Any, dict[str, Any]] = {}
        plain: list[dict[str, Any]] = []
        for row in kept:
            if "version" not in row:
                plain.append(row)
                continue
            key = (_as_date(row.get("observation_date")), row.get("source_id"))
            if key not in newest or (row.get("version") or 0) > (newest[key].get("version") or 0):
                if key in newest:
                    hidden += 1
                newest[key] = row
            else:
                hidden += 1
        kept = plain + list(newest.values())
    return kept, hidden


def interpolate_internal_gaps(values: dict[date, float], cutoff: date, frequency: str,
                              max_gap: int = 2) -> tuple[dict[date, float], list[date], list[date]]:
    """Fill a blank period only when both its neighbours are known AT THE CUTOFF.

    `values` must already be what the cutoff allows (see `visible_rows`); this function fills
    straight-line between the nearest known periods on either side, up to `max_gap` consecutive
    blanks. A blank run that reaches the cutoff has no right endpoint and stays blank. Returns the
    filled series, the periods filled, and the periods left missing.
    """
    if not values:
        return {}, [], []
    known = dict(sorted(values.items()))
    first, last = min(known), max(known)
    filled: dict[date, float] = {}
    interpolated: list[date] = []
    missing: list[date] = []
    expected = expected_periods(first, last, frequency)
    run: list[date] = []
    previous: date | None = None
    for day in expected:
        if day in known:
            if run:
                if previous is not None and len(run) <= max_gap:
                    left, right = known[previous], known[day]
                    for k, blank in enumerate(run, start=1):
                        filled[blank] = left + (right - left) * k / (len(run) + 1)
                        interpolated.append(blank)
                else:
                    missing.extend(run)
                run = []
            filled[day] = known[day]
            previous = day
        else:
            run.append(day)
    # A run after the last known period cannot exist within [first, last]; a blank period between
    # the last print and the cutoff is accounted for by the caller as `latest_missing`.
    missing.extend(run)
    return dict(sorted(filled.items())), interpolated, missing


def build_history(rows: Sequence[dict[str, Any]], cutoff: date, *, identity_key: str, chemical: str,
                  frequency: str | None = None, unit: str = "", max_gap: int = 2) -> HistoricalSeries:
    """The target series as it could have been read on `cutoff`.

    `frequency` fixes the working frequency; left None, the engine's own `best_frequency` decides on
    the visible rows, which is what the live forecast does. The series' `offset_days` is set the way
    the live path sets it, so a calendar horizon resolves to the same steps.
    """
    visible, hidden = visible_rows(rows, cutoff)
    if frequency is None:
        frequency, points, _why = best_frequency(visible)
    else:
        points = bucket(visible, frequency)
    # Ending on the newest price the cutoff allows, not on the median of a period still being
    # printed: see `service.ending_on_the_newest_price`. Here rather than in each caller, because
    # this is the one history the tournament examines a method on AND production asks it with.
    points = ending_on_the_newest_price(points, visible, frequency, cutoff)
    filled, interpolated, missing = interpolate_internal_gaps(points, cutoff, frequency, max_gap)
    newest = max((_as_date(r.get("observation_date")) for r in visible), default=None)
    last_bucket = max(filled) if filled else None
    offset = (newest - last_bucket).days if newest and last_bucket and newest > last_bucket else 0
    series = Series(identity_key, chemical, frequency, tuple(filled.items()), unit=unit or MT,
                    offset_days=offset)
    expected = expected_periods(min(filled), max(filled), frequency) if filled else []
    current_period = _period(cutoff, frequency)
    # The period the cutoff stands in. Blank means the newest print the reader has is older than
    # the period they are standing in - the "missing latest observation" of Review Focus 1.
    latest_missing = bool(filled) and last_bucket < current_period
    return HistoricalSeries(
        series=series, cutoff=cutoff, frequency=frequency, expected_periods=len(expected),
        observed_periods=len(points), interpolated=tuple(interpolated), missing=tuple(missing),
        latest_missing=latest_missing, rows_hidden=hidden)


def truth_at(rows: Sequence[dict[str, Any]], period: date, frequency: str, *, known_by: date,
             complete_only: bool = True) -> float | None:
    """The bucketed value for `period`, if it had landed by `known_by`. None otherwise.

    A tournament scores a forecast only against an outcome that was readable by the review cutoff;
    an outcome that arrives later is scored by a later review. `complete_only` also waits for the
    period to be over: a month half-printed at the cutoff is not yet the month's value.
    """
    if complete_only and period_end(period, frequency) > known_by:
        return None
    visible, _hidden = visible_rows(rows, known_by)
    return bucket(visible, frequency).get(_period(period, frequency))
