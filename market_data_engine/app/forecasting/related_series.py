"""Which other series a conditional method may lean on, decided on what was visible at the cutoff.

`select_related_series(target, candidates, cutoff, max_series)` ranks candidate series by how their
period-on-period changes have moved with the target's - on the overlap that existed AT THE CUTOFF.
Every candidate is cut at the cutoff before anything is computed, so a correlation that only
appears later cannot pull a series into an earlier choice (plan, Task 5, step 1).

Changes rather than levels, because two prices that both drift upward for three years are
"correlated" at 0.95 without one telling you anything about the other's next move. Log changes,
because a 10% move is a 10% move in CNY/t and in EUR/t alike. A candidate needs a minimum overlap
to be ranked at all; a correlation on eight shared periods is a coincidence with a decimal point.

The ranking is deterministic: by |correlation| descending, then by overlap descending, then by key.
Two series with the same numbers rank in the same order on every machine, which is what lets a
review be rerun and reproduced.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from app.forecasting.adapter import Series

MIN_OVERLAP = 24
MIN_ABS_CORRELATION = 0.30


@dataclass(frozen=True)
class RelatedSeries:
    key: str
    series: Series                  # already cut at the cutoff
    correlation: float
    overlap: int
    lag: int = 0                    # periods the candidate leads the target; 0 = contemporaneous
    # Periods between this series' newest published period and the target's at the origin: the
    # publication lag a conditional method applies to it (decision D2). Recorded per origin by the
    # tournament (v0.5.60, gate finding 4); None when not measured.
    publication_lag: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "correlation": round(self.correlation, 4), "overlap": self.overlap,
                "lag": self.lag, "publication_lag": self.publication_lag, "last_period": self.series.last_date.isoformat() if self.series.last_date else None}


def log_changes(series: Series) -> dict[date, float]:
    points = [(d, v) for d, v in series.points if v > 0]
    return {later[0]: math.log(later[1] / earlier[1]) for earlier, later in zip(points, points[1:])}


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def correlation_at(target: Series, candidate: Series, cutoff: date, lag: int = 0) -> tuple[float | None, int]:
    """Correlation of changes on the shared periods up to the cutoff, and how many there were.

    `lag` shifts the candidate earlier by that many of ITS periods: lag 1 asks whether the
    candidate's last change moved with the target's next one. Both series must share a frequency
    for a lag to mean anything; a mismatch is compared contemporaneously by period start.
    """
    target_changes = log_changes(target.up_to(cutoff))
    candidate_changes = log_changes(candidate.up_to(cutoff))
    if lag:
        days = sorted(candidate_changes)
        shifted = {}
        for index, day in enumerate(days):
            if index + lag < len(days):
                shifted[days[index + lag]] = candidate_changes[day]
        candidate_changes = shifted
    shared = sorted(set(target_changes) & set(candidate_changes))
    if not shared:
        return None, 0
    return _pearson([target_changes[d] for d in shared], [candidate_changes[d] for d in shared]), len(shared)


def select_related_series(target: Series, candidates: Sequence[Series], cutoff: date, max_series: int = 3, *,
                          min_overlap: int = MIN_OVERLAP, min_abs_correlation: float = MIN_ABS_CORRELATION,
                          lags: Sequence[int] = (0,)) -> list[RelatedSeries]:
    """The strongest related series visible at the cutoff, at most `max_series`, best first."""
    ranked: list[RelatedSeries] = []
    for candidate in candidates:
        if candidate.key == target.key:
            continue
        cut = candidate.up_to(cutoff)
        best: RelatedSeries | None = None
        for lag in lags:
            rho, overlap = correlation_at(target, cut, cutoff, lag)
            if rho is None or overlap < min_overlap or abs(rho) < min_abs_correlation:
                continue
            found = RelatedSeries(candidate.key, cut, rho, overlap, lag)
            if best is None or (abs(found.correlation), found.overlap) > (abs(best.correlation), best.overlap):
                best = found
        if best is not None:
            ranked.append(best)
    ranked.sort(key=lambda r: (-abs(r.correlation), -r.overlap, r.key))
    return ranked[:max_series]
