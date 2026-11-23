"""Matched-date scoring and the ranking rule (plan, Task 6).

Every method is marked on the SAME origins - the exam `compare.on_common_origins` already sets for
the page's harness, reused here - and ranked by:

1. matched MAE, at stored precision (two decimals, the precision the vintage store keeps), lowest first;
2. directional accuracy, highest first, only when the stored-precision MAE is equal;
3. the method key, so two methods that tie on both still come out in one order on every machine.

A method that answered too few of the scoreable origins is not ranked. Not because it did badly, but
because admitting it would shrink everyone's exam to the handful of dates it managed - the Chinese
TDI case of 14 September 2026 in the compare module's docstring, where four origins reversed a
ranking made on twenty-one. It is reported, with its count, as unranked.

Statuses are counted per method so a review can say how often a method was INELIGIBLE, FAILED or
UNAVAILABLE across the origins, which is a different fact from its accuracy on the ones it answered.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from app.forecasting.adapters.base import ExecutionStatus
from app.forecasting.compare import Score, Trial, direction_right, on_common_origins

STORED_PRECISION = 2
# A method must have answered at least this share of the origins scored at a horizon to be ranked.
# 0.9 since v0.5.60 (acting-architect decision D6, gate finding 7): every ranked method is marked on
# the dates ALL of them answered, so a method that sits out the hard dates shrinks everyone's exam to
# the easy ones. At 90% a method can remove at most a tenth of the exam; one that cannot answer more
# often than that is not ready to be a production forecast for that series anyway.
MIN_ANSWERED_SHARE = 0.9


@dataclass(frozen=True)
class Outcome:
    """One method, one origin, one horizon: what it said, what happened, and what status it reported."""

    method_key: str
    method_version: str
    origin: date
    horizon_weeks: int
    status: ExecutionStatus
    forecast: float | None = None
    low: float | None = None
    high: float | None = None
    origin_value: float | None = None
    truth: float | None = None               # None = not matured by the review cutoff
    reason: str = ""

    @property
    def scoreable(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED and self.forecast is not None and self.truth is not None

    @property
    def abs_error(self) -> float | None:
        return abs(self.truth - self.forecast) if self.scoreable else None


@dataclass
class MethodScore:
    method_key: str
    method_version: str
    horizon_weeks: int
    ranked: bool
    matched_dates: int
    own_dates: int
    mae: float | None = None
    mape: float | None = None
    directional_accuracy: float | None = None
    coverage: float | None = None
    win_rate_vs_control: float | None = None
    rank: int | None = None
    statuses: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method_key, "version": self.method_version, "horizon_weeks": self.horizon_weeks,
            "ranked": self.ranked, "rank": self.rank, "matched_dates": self.matched_dates, "own_dates": self.own_dates,
            "mae": self.mae, "mape": self.mape, "directional_accuracy": self.directional_accuracy,
            "coverage": self.coverage, "win_rate_vs_control": self.win_rate_vs_control,
            "statuses": dict(self.statuses), "note": self.note,
        }


def _scores(outcomes: Iterable[Outcome], control_key: str) -> dict[tuple[str, int], Score]:
    """compare.Score per (method, horizon), built from scoreable outcomes."""
    by_key: dict[tuple[str, int], Score] = {}
    control_error: dict[tuple[date, int], float] = {}
    rows = [o for o in outcomes if o.scoreable]
    for o in rows:
        if o.method_key == control_key:
            control_error[(o.origin, o.horizon_weeks)] = o.abs_error
    for o in rows:
        score = by_key.setdefault((o.method_key, o.horizon_weeks), Score(o.method_key, o.horizon_weeks))
        score.origins += 1
        score.abs_errors.append(o.abs_error)
        if o.truth:
            score.pct_errors.append(abs((o.truth - o.forecast) / o.truth) * 100.0)
        inside = None
        if o.low is not None and o.high is not None:
            score.banded += 1
            inside = bool(o.low <= o.truth <= o.high)
            score.inside += int(inside)
        beat = None
        reference = control_error.get((o.origin, o.horizon_weeks))
        if reference is not None and o.method_key != control_key:
            score.compared += 1
            beat = o.abs_error < reference
            score.wins += int(beat)
        score.trials[o.origin] = Trial(
            abs_error=o.abs_error,
            pct_error=(abs((o.truth - o.forecast) / o.truth) * 100.0) if o.truth else None,
            inside=inside, beat_control=beat,
            direction_right=direction_right(o.origin_value, o.forecast, o.truth))
    return by_key


def rank_methods(outcomes: Sequence[Outcome], horizons: Sequence[int], *, control_key: str = "naive_last",
                 min_answered_share: float = MIN_ANSWERED_SHARE) -> dict[int, list[MethodScore]]:
    """The ranking per horizon. Deterministic for the same outcomes."""
    versions = {o.method_key: o.method_version for o in outcomes}
    status_counts: dict[tuple[str, int], Counter] = {}
    why: dict[tuple[str, int], Counter] = {}
    for o in outcomes:
        status_counts.setdefault((o.method_key, o.horizon_weeks), Counter())[o.status.value] += 1
        if o.reason:
            why.setdefault((o.method_key, o.horizon_weeks), Counter())[o.reason] += 1

    def _why(key: str, weeks: int) -> str:
        """The commonest reason a method did not answer, so a review says why, not only how often (v0.5.58)."""
        counts = why.get((key, weeks))
        if not counts:
            return ""
        text, n = counts.most_common(1)[0]
        return f"; most often: {text[:200]} ({n}x)"
    scores = _scores(outcomes, control_key)
    out: dict[int, list[MethodScore]] = {}
    for weeks in horizons:
        rows = {key[0]: score for key, score in scores.items() if key[1] == weeks}
        matured = {o.origin for o in outcomes if o.horizon_weeks == weeks and o.truth is not None}
        # Who sits the exam: methods that answered enough of the matured origins.
        sitting = {key: score for key, score in rows.items()
                   if matured and score.origins / len(matured) >= min_answered_share}
        table = on_common_origins({(k, weeks): s for k, s in sitting.items()}, (weeks,)) if sitting else []
        by_method = {row["model"]: row for row in table}
        ranked: list[MethodScore] = []
        for key, score in rows.items():
            counts = dict(status_counts.get((key, weeks), {}))
            if key in by_method and by_method[key]["mae"] is not None:
                row = by_method[key]
                ranked.append(MethodScore(key, versions.get(key, ""), weeks, True, row["common_origins"], score.origins,
                                          mae=row["mae"], mape=row["mape"],
                                          directional_accuracy=row["directional_accuracy"], coverage=row["coverage"],
                                          win_rate_vs_control=row["win_rate_vs_naive"], statuses=counts))
            else:
                ranked.append(MethodScore(key, versions.get(key, ""), weeks, False, 0, score.origins, statuses=counts,
                                          note=(f"answered {score.origins} of {len(matured)} matured origins; "
                                                f"fewer than {min_answered_share:.0%} - not ranked{_why(key, weeks)}")))
        # Methods that never succeeded at this horizon: present in the table with their statuses.
        for (key, h), counts in status_counts.items():
            if h == weeks and key not in rows:
                most = counts.most_common(1)[0][0] if counts else ""
                ranked.append(MethodScore(key, versions.get(key, ""), weeks, False, 0, 0, statuses=dict(counts),
                                          note=f"no scoreable answer at this horizon ({most}){_why(key, weeks)}"))
        ranked.sort(key=lambda m: (not m.ranked, round(m.mae, STORED_PRECISION) if m.mae is not None else float("inf"),
                                   -(m.directional_accuracy or 0.0), m.method_key))
        position = 0
        for method in ranked:
            if method.ranked:
                position += 1
                method.rank = position
        out[weeks] = ranked
    return out
