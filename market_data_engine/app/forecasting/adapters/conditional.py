"""What every conditional method shares: it needs something beyond the target, visible at the cutoff.

A conditional method is INELIGIBLE - not FAILED - when its supporting input is absent for this
series at this origin (plan, Task 5; Review Focus 2). The universal methods keep running; the
conditional one simply has nothing to say here, and the tournament records that as its own status
so a review can count how often each method was even in the running.

The alignment helpers here build the design matrix the conditional methods fit on: the target's
periods, each with the related series' values at the same period start, dropped where any input is
blank. Nothing is forward-filled across the cutoff; a related series that stops before the target's
last period leaves the last rows blank, and a method that needs them says so.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Sequence

from app.forecasting.adapter import Series, period_after
from app.forecasting.adapters.base import ForecastContext, ForecastModelAdapter, Ineligible

MIN_RELATED_OVERLAP = 24


class ConditionalAdapter(ForecastModelAdapter):
    kind = "CONDITIONAL"
    needs = "related series visible at the cutoff"
    min_related = 1

    def related(self, context: ForecastContext) -> tuple[Series, ...]:
        long_enough = [s for s in context.related if len(s.points) >= MIN_RELATED_OVERLAP]
        usable = at_publication_lag(context.history, long_enough)
        if len(usable) < self.min_related and len(long_enough) >= self.min_related:
            stale = [s.key for s in long_enough if s.key not in {u.key for u in usable}]
            raise Ineligible(f"related series {', '.join(stale)} end more than {MAX_PUBLICATION_LAG} periods "
                             f"before the target's last period at {context.cutoff.isoformat()} (stale, not lagging)")
        if len(usable) < self.min_related:
            raise Ineligible(f"needs {self.min_related} related series with {MIN_RELATED_OVERLAP}+ periods "
                             f"visible at {context.cutoff.isoformat()}; has {len(usable)}")
        return usable


MAX_PUBLICATION_LAG = 4      # periods; a related series further behind than this is stale, not lagging


def at_publication_lag(target: Series, candidates: Sequence[Series],
                       max_lag: int = MAX_PUBLICATION_LAG) -> tuple[Series, ...]:
    """Each related series moved forward by its own publication lag (acting-architect decision D2, 20 Oct 2026).

    Upstream series - toluene, benzene, propylene oxide, customs - are published later than the Asian
    price feeds. At an origin the target's newest period is t and a related series' newest is t - L.
    The related series then enters as a lagged regressor: the target at t is paired with the related
    value at t - L, by relabelling the related series L periods later. Every value used was published
    by the cutoff (the related series was cut there before it reached this function), so nothing from
    the future enters; the lag is the gap visible at the cutoff and is named in the series label.

    A series that is not on the target's period grid, or is more than `max_lag` periods behind, is left
    out - it is stale at this origin, not lagging.
    """
    end = target.last_date
    if end is None:
        return ()
    out: list[Series] = []
    for series in candidates:
        if series.last_date is not None and series.last_date > end:
            # A supporting series published FASTER than the target (the target is asked on its own,
            # late, publication day): cut it at the target's last period for alignment. Lag 0.
            # (v0.5.61, gate condition 1 - it used to be dropped, so conditional methods could never
            # run on a late-published target.)
            series = series.up_to(end)
        last = series.last_date
        if last is None:
            continue
        lag, day = 0, last
        while day < end and lag <= max_lag:
            day = period_after(day, 1, target.frequency)
            lag += 1
        if day != end or lag > max_lag:
            continue
        if lag == 0:
            out.append(series)
            continue
        out.append(Series(series.key, series.chemical, series.frequency,
                          tuple((period_after(d, lag, target.frequency), v) for d, v in series.points),
                          series.unit, f"{series.key} at lag {lag}", series.offset_days))
    return tuple(out)


def aligned_log_levels(target: Series, related: Sequence[Series]) -> tuple[list[date], list[float], list[list[float]]]:
    """(periods, log target, [log related...]) on the periods every series has, in order."""
    columns = [dict(s.points) for s in related]
    days, y, x = [], [], []
    for day, value in target.points:
        if value <= 0 or any(day not in col or col[day] <= 0 for col in columns):
            continue
        days.append(day)
        y.append(math.log(value))
        x.append([math.log(col[day]) for col in columns])
    return days, y, x


def least_squares(rows: Sequence[Sequence[float]], y: Sequence[float], ridge: float = 1e-6) -> list[float]:
    """Ordinary least squares with an intercept, solved by Gaussian elimination. Pure Python on
    purpose: a native statistics library was blocked by Windows Application Control on 25
    September 2026 and took the application down."""
    n = len(rows)
    if n == 0:
        return []
    k = len(rows[0]) + 1
    X = [[1.0, *row] for row in rows]
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) + (ridge if a == b else 0.0) for b in range(k)] for a in range(k)]
    B = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(A[r][col]))
        A[col], A[pivot] = A[pivot], A[col]
        B[col], B[pivot] = B[pivot], B[col]
        if abs(A[col][col]) < 1e-12:
            return [0.0] * k
        for r in range(k):
            if r != col:
                factor = A[r][col] / A[col][col]
                A[r] = [a - factor * b for a, b in zip(A[r], A[col])]
                B[r] -= factor * B[col]
    return [B[i] / A[i][i] for i in range(k)]


def empirical_band(residuals: Sequence[float], value: float, low: float = 0.1, high: float = 0.9) -> dict[float, float]:
    """A 10-90 band around `value` from the fit's own residuals (in the value's units)."""
    if len(residuals) < 5:
        return {}
    ordered = sorted(residuals)
    def pick(q: float) -> float:
        pos = q * (len(ordered) - 1)
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return {low: value + pick(low), 0.5: value, high: value + pick(high)}
