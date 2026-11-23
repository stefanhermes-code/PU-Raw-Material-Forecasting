"""The governed tournament: every method, every origin, one exam, cutoff-safe (plan, Task 6).

`run_tournament` rolls the origin through the target's history exactly as the page's harness does
(`compare.rolling_origin`), with three things the governed version adds:

- **the history is rebuilt at every origin** from the rows' availability (`history.build_history`),
  not cut from today's series by observation date - so a customs month published in June is absent
  from an April origin, and an internal gap is filled only from neighbours known at that origin;
- **every status is recorded**: a method that raised is FAILED at that origin, one that declined is
  INELIGIBLE, one whose runtime is missing is UNAVAILABLE - and none of them is replaced by another
  method or quietly skipped (plan, Task 6, step 3);
- **an outcome counts only once it has landed by the review cutoff** (`history.truth_at`), so a
  review run on 1 October cannot score a September origin's twelve-week answer.

An origin whose latest period is blank (the "missing latest observation" of Review Focus 1) is
skipped for every method and listed as skipped, with the reason; nothing is interpolated into it.

Related series and features are cut at each origin and the related-series choice is remade at each
origin from what was visible then (`related_series.select_related_series`), so a conditional method
at a 2024 origin leans on what 2024 could see.

This runs nothing on the page path and chooses nothing. `selection.propose` reads its result.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import perf_counter
from datetime import date
from typing import Any, Mapping, Sequence

from app.forecasting.adapter import Series, period_after, steps_for
from app.forecasting.adapters.base import ExecutionStatus, ForecastContext, ForecastModelAdapter
from app.forecasting.compare import MIN_TRAIN
from app.forecasting.history import HistoricalSeries, build_history, period_end, truth_at
from app.forecasting.related_series import RelatedSeries, select_related_series
from app.forecasting.scoring import MethodScore, Outcome, rank_methods
from app.forecasting.service import _as_date, _period, availability_by_period


@dataclass(frozen=True)
class SkippedOrigin:
    origin: date
    reason: str


@dataclass
class TournamentResult:
    identity_key: str
    chemical: str
    cutoff: date
    horizons: tuple[int, ...]
    frequency: str
    history: HistoricalSeries                       # as it stood at the cutoff
    origins: list[date] = field(default_factory=list)
    skipped: list[SkippedOrigin] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    related_by_origin: dict[date, list[RelatedSeries]] = field(default_factory=dict)
    # Supporting series left out at an origin because their feed had stopped (gate finding 5):
    # behind by more than their own usual publication lag. Key -> reason, per origin.
    stopped_by_origin: dict[date, dict[str, str]] = field(default_factory=dict)
    ranking: dict[int, list[MethodScore]] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    # Seconds spent inside each method's own `forecast`, summed over every origin.
    #
    # **The stopwatch masterplan H asks for first.** The review of 20 October took 6.41 hours for
    # 2,040 fits - 11.3 seconds each on average - and recorded ONE elapsed time for the whole run.
    # So "27% of the fits produce nothing to rank and 12% are on retired methods" could be said by
    # count and not by cost, and a trivial `naive_last` fit and a Chronos fit are not the same 11.3
    # seconds. Until this was recorded, any decision to drop a method from the review was a guess
    # about where the hours went. H: "the first concrete step is not a cut, it is a stopwatch".
    seconds_by_method: dict[str, float] = field(default_factory=dict)

    def ranked(self, horizon_weeks: int) -> list[MethodScore]:
        return [m for m in self.ranking.get(horizon_weeks, []) if m.ranked]

    def statuses(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for o in self.outcomes:
            out.setdefault(o.method_key, {}).setdefault(o.status.value, 0)
            out[o.method_key][o.status.value] += 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key, "chemical": self.chemical, "cutoff": self.cutoff.isoformat(),
            "horizons": list(self.horizons), "frequency": self.frequency, "history": self.history.as_dict(),
            "origins": [d.isoformat() for d in self.origins],
            "skipped": [{"origin": s.origin.isoformat(), "reason": s.reason} for s in self.skipped],
            "statuses": self.statuses(),
            "seconds_by_method": dict(sorted(self.seconds_by_method.items())),
            "ranking": {str(h): [m.as_dict() for m in rows] for h, rows in self.ranking.items()},
            "related": {d.isoformat(): [r.as_dict() for r in rows] for d, rows in self.related_by_origin.items()},
            "stopped_supporting_series": {d.isoformat(): dict(v) for d, v in self.stopped_by_origin.items() if v},
            "settings": dict(self.settings),
        }


def _visible_series(rows: Sequence[dict[str, Any]], asked: date, key: str, frequency: str) -> Series | None:
    """A supporting series as it could be read on `asked`, at the target's frequency."""
    built = build_history(rows, asked, identity_key=key, chemical=key, frequency=frequency)
    return built.series if built.series.points else None


def _feed_stopped(rows: Sequence[dict[str, Any]], built: Series, asked: date, frequency: str,
                  window: int = 12, slack: int = 1) -> str:
    """Why a supporting feed counts as STOPPED at `asked`, or "" when it is current or merely late.

    Decision D2 lets a related series enter at its publication lag. That must not turn a dead feed into
    a "late" one (gate finding 5, Review Focus 2). A feed's usual lag is measured from its own rows
    readable by `asked`: for its newest `window` periods, how many periods passed between the period
    and the day it was published. If the newest period is now further behind `asked` than that usual
    lag plus `slack`, the feed has stopped and the method must not lean on it at this origin.
    """
    if built.last_date is None:
        return "no visible history"
    lags: list[int] = []
    for row in rows:
        day, moment = _as_date(row.get("observation_date")), _as_date(row.get("available_from"))
        if day is None or moment is None or moment > asked:
            continue
        gap = _periods_between(_period(day, frequency), _period(moment, frequency), frequency)
        if gap is not None:
            lags.append((day, gap))
    lags = [g for _d, g in sorted(lags)[-window:]]
    usual = sorted(lags)[len(lags) // 2] if lags else 0
    # The slack is measured in the feed's OWN print interval (v0.5.61, gate condition 2): a monthly
    # feature beside a weekly target prints every ~4 target periods, and an event count has quiet
    # weeks; one target period of slack called such feeds stopped half the time.
    gaps = sorted(g for a, b in zip(built.dates, built.dates[1:])
                  if (g := _periods_between(a, b, frequency)) is not None)[-window:] if len(built.dates) > 1 else []
    interval = sorted(gaps)[len(gaps) // 2] if gaps else 1
    slack = max(slack, interval)
    behind = _periods_between(built.last_date, _period(asked, frequency), frequency)
    if behind is None:
        return ""
    if behind > usual + slack:
        return (f"newest period {built.last_date.isoformat()} is {behind} periods behind {asked.isoformat()}; "
                f"this feed is usually {usual} behind - stopped, not late")
    return ""


def _publication_lag(related_last: date | None, target_last: date | None, frequency: str) -> int | None:
    """Periods the related series' newest period trails the target's; negative when it is AHEAD (it is
    then cut at the target's last period and used at lag 0). None when off-grid (v0.5.61)."""
    behind = _periods_between(related_last, target_last, frequency)
    if behind is not None:
        return behind
    ahead = _periods_between(target_last, related_last, frequency)
    return -ahead if ahead is not None else None


def _periods_between(earlier: date | None, later: date | None, frequency: str, limit: int = 60) -> int | None:
    """Whole periods from `earlier` to `later` on the series' grid; None if off-grid or unknown."""
    if earlier is None or later is None or earlier > later:
        return None
    steps, day = 0, earlier
    while day < later and steps <= limit:
        day = period_after(day, 1, frequency)
        steps += 1
    return steps if day == later else None


def _visible_or_stopped(rows: Sequence[dict[str, Any]], asked: date, key: str, frequency: str,
                        cache: dict | None) -> tuple[Series | None, str]:
    """A supporting series as readable on `asked` and why it is stopped ("" if it is not), memoised.

    The same related series is rebuilt at the same asked dates for every target in a review - 330
    markets times every origin - and the answer depends only on (key, asked, frequency). v0.5.62:
    a review passes one cache for its whole run, which cut ~6 minutes per market in the 20 Oct run."""
    token = (key, asked, frequency)
    if cache is not None and token in cache:
        return cache[token]
    built = _visible_series(rows, asked, key, frequency)
    answer = (None, "") if built is None else (built, _feed_stopped(rows, built, asked, frequency))
    if cache is not None:
        cache[token] = answer
    return answer


def first_published(rows: Sequence[dict[str, Any]], frequency: str) -> dict[date, date]:
    """When each period could FIRST be read whole: per dated value its first version (not a later
    revision), and per period the latest of those. One rule for the backtest and production (WP3)."""
    first: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = (_as_date(row.get("observation_date")), row.get("source_id"))
        if key not in first or (row.get("version") or 0) < (first[key].get("version") or 0):
            first[key] = row
    return availability_by_period(list(first.values()), frequency)


def asked_on(period: date, frequency: str, published: Mapping[date, date]) -> date:
    """The day a question standing on `period` is asked: when the period has ended AND been published."""
    return max(period_end(period, frequency), published.get(period, period))


def supporting_at(target: Series, asked: date, frequency: str, *,
                  related_candidates: Sequence[Series] = (), features: Sequence[Series] = (),
                  related_rows: Mapping[str, Sequence[dict[str, Any]]] | None = None,
                  feature_rows: Mapping[str, Sequence[dict[str, Any]]] | None = None,
                  max_related: int = 3, cache: dict | None = None) -> tuple[list[RelatedSeries], list[Series], dict[str, str]]:
    """The related series and features a method may lean on when asked on `asked`, and the feeds left
    out as stopped. ONE function for the backtest origin and the production forecast (WP3), so the
    live forecast is built exactly as the method was examined."""
    pool = [c.up_to(asked) for c in related_candidates]
    stopped: dict[str, str] = {}
    for key, other in (related_rows or {}).items():
        built, why = _visible_or_stopped(other, asked, key, frequency, cache)
        if built is None:
            continue
        if why:
            stopped[key] = why
        else:
            pool.append(built)
    related = select_related_series(target, pool, asked, max_related) if pool else []
    related = [replace(r, publication_lag=_publication_lag(r.series.last_date, target.last_date, frequency))
               for r in related]
    visible_features = [f.up_to(asked) for f in features]
    for key, other in (feature_rows or {}).items():
        # Not memoised: feature rows are built per target chemical (its own event counts).
        built, why = _visible_or_stopped(other, asked, key, frequency, None)
        if built is None:
            continue
        if why:
            stopped[key] = why
        else:
            visible_features.append(built)
    return related, visible_features, stopped


def run_tournament(rows: Sequence[dict[str, Any]], *, identity_key: str, chemical: str, cutoff: date,
                   adapters: Sequence[ForecastModelAdapter], horizons: tuple[int, ...] = (4, 8, 12),
                   min_train: int = MIN_TRAIN, max_origins: int | None = None,
                   related_candidates: Sequence[Series] = (), features: Sequence[Series] = (),
                   related_rows: Mapping[str, Sequence[dict[str, Any]]] | None = None,
                   feature_rows: Mapping[str, Sequence[dict[str, Any]]] | None = None,
                   frequency: str | None = None, max_related: int = 3, unit: str = "",
                   control_key: str = "naive_last", cache: dict | None = None) -> TournamentResult:
    """Backtest `adapters` on this series at every origin up to `cutoff`; rank per horizon.

    Supporting series come two ways. `related_candidates`/`features` are ready `Series`, cut at each
    origin by date. `related_rows`/`feature_rows` are store rows keyed by series key, rebuilt at each
    origin from their own `available_from` - the form the review job uses, because a related series
    published late must be as absent at an origin as the target would be.
    """
    at_cutoff = build_history(rows, cutoff, identity_key=identity_key, chemical=chemical,
                              frequency=frequency, unit=unit)
    frequency = at_cutoff.frequency
    result = TournamentResult(identity_key, chemical, cutoff, tuple(horizons), frequency, at_cutoff,
                              settings={"min_train": min_train, "max_origins": max_origins,
                                        "methods": [a.method_key for a in adapters],
                                        "related_candidates": [s.key for s in related_candidates] + sorted(related_rows or {}),
                                        "features": [s.key for s in features] + sorted(feature_rows or {})})
    days = list(at_cutoff.series.dates)
    if len(days) <= min_train:
        result.settings["note"] = f"{len(days)} periods at the cutoff; {min_train + 1} needed for one origin"
        result.ranking = rank_methods([], horizons, control_key=control_key)
        return result
    # Only to choose which origins are worth asking; the exam period is worked out per origin below.
    steps_by_horizon = {weeks: steps_for(weeks, frequency, 0)[0] for weeks in horizons}
    shortest = min(steps_by_horizon.values())
    # An origin is worth asking only if at least its shortest horizon can have landed by the cutoff.
    candidates = [day for index, day in enumerate(days) if index >= min_train
                  and period_after(day, shortest, frequency) <= days[-1]]
    if max_origins is not None:
        candidates = candidates[-max_origins:]

    published = first_published(rows, frequency)
    for origin in candidates:
        # The origin is a bucket; the question is asked when that bucket could first be read whole:
        # the END of its period for a publisher that prints as it goes, the day it was PUBLISHED for
        # one that prints late (v0.5.60, gate finding 3). Asking a customs month at its own month-end
        # found it blank every time and skipped every origin of every customs series, so a series the
        # eligibility rules admit could never be ranked. Capped at the review cutoff.
        asked = min(asked_on(origin, frequency, published), cutoff)
        history = build_history(rows, asked, identity_key=identity_key, chemical=chemical,
                                frequency=frequency, unit=unit)
        if not history.series.points:
            result.skipped.append(SkippedOrigin(origin, "no visible history at this origin"))
            continue
        if history.series.last_date > origin:
            result.skipped.append(SkippedOrigin(
                origin, f"a later period ({history.series.last_date.isoformat()}) was already readable when this "
                        f"one was, on {asked.isoformat()}; it is that period's origin, not this one's"))
            continue
        if history.series.last_date != origin:
            # Nothing for this period was readable by the end of it: the "missing latest
            # observation" of Review Focus 1. Skipped for every method, filled from nothing.
            result.skipped.append(SkippedOrigin(
                origin, f"period blank when asked on {asked.isoformat()} (last visible "
                        f"{history.series.last_date.isoformat()}); not interpolated from later data"))
            continue
        related, visible_features, stopped = supporting_at(
            history.series, asked, frequency, related_candidates=related_candidates, features=features,
            related_rows=related_rows, feature_rows=feature_rows, max_related=max_related, cache=cache)
        result.related_by_origin[origin] = related
        result.stopped_by_origin[origin] = stopped
        context = ForecastContext(identity_key, chemical, asked, history.series, tuple(horizons),
                                  tuple(r.series for r in related), tuple(visible_features))
        result.origins.append(origin)
        origin_value = history.series.points[-1][1]
        # The period each horizon lands in, worked out EXACTLY as the methods work it out: from the
        # history at this origin, whose newest print sits `offset_days` past its bucket. It used to be
        # worked out once from the history at the review cutoff, whose offset is different, so every
        # method was scored against a neighbouring period (gate finding 1, 20 Oct 2026).
        target_by_horizon = {weeks: period_after(origin, steps_for(weeks, frequency, history.series.offset_days)[0],
                                                 frequency) for weeks in horizons}
        for adapter in adapters:
            # Round the `forecast` call and nothing else: the scoring below is the same handful of
            # arithmetic for every method, and attributing it to one of them would flatter the
            # expensive ones.
            began = perf_counter()
            output = adapter.forecast(context)
            result.seconds_by_method[adapter.method_key] = round(
                result.seconds_by_method.get(adapter.method_key, 0.0) + (perf_counter() - began), 3)
            for weeks in horizons:
                target = target_by_horizon[weeks]
                truth = truth_at(rows, target, frequency, known_by=cutoff)
                point = output.at(weeks) if output.succeeded else None
                if output.succeeded and point is None:
                    status, reason = ExecutionStatus.INELIGIBLE, f"did not answer the {weeks}-week horizon"
                elif point is not None and point.period != target:
                    # A method that answered a different period than the one it is scored on would be
                    # marked on a question it was not asked. Recorded, never scored.
                    status, reason = ExecutionStatus.FAILED, (
                        f"answered period {point.period.isoformat()} for {weeks} weeks; the exam period is "
                        f"{target.isoformat()}")
                    point = None
                else:
                    status, reason = output.status, output.reason
                result.outcomes.append(Outcome(
                    output.method_key, output.method_version, origin, weeks, status,
                    forecast=None if point is None else float(point.value),
                    low=None if point is None else point.quantiles.get(0.1),
                    high=None if point is None else point.quantiles.get(0.9),
                    origin_value=origin_value, truth=truth, reason=reason))
    result.ranking = rank_methods(result.outcomes, horizons, control_key=control_key)
    return result
