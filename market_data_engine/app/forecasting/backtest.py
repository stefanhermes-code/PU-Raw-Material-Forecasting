from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Iterable

from app.forecasting.readiness import assess_readiness

# The horizons the product sells. `/forecast` defaults to these three weeks ahead and the forecast
# record is scored on them, so the controls are scored on them too.
#
# They were scored one row ahead, which is one *reading* rather than one period: a day on a daily
# series, a month on a monthly one. So the average miss under Controls was a one-day miss on most
# markets, printed directly beneath a record of 1, 2 and 3-month calls, with nothing saying the two
# numbers were not comparable. A carry-forward control is almost unbeatable over a day and loses
# badly over a quarter, which is the whole reason to have it - at one day ahead it flattered every
# method it was meant to test (Stefan, 31 October 2026: "So what do we change?").
HORIZON_WEEKS = (4, 8, 12)

# A reading stands as "the price at the target date" when it lands within the gap this series
# normally leaves, never less than a long weekend: a Saturday target must not cost a comparison on
# a weekday-daily series. Measured from the series itself, because a monthly assessment cannot be
# held to a daily calendar - the same rule the collection watch judges silence by.
MATCH_SLACK_DAYS = 3

# An average miss drawn from one or two comparisons is noise beside one drawn from a hundred. The
# gate above this already demands 540 days, so no real market is held back by this.
MIN_COMPARISONS = 6

# Named twice on purpose: the key identifies the method and the name is what a reader is shown.
# "NAIVE_LAST" was appearing verbatim on the Forecast Performance screen, where it told a buyer
# nothing they could weigh.
CONTROLS = (("NAIVE_LAST", "No change"),
            ("MOVING_AVERAGE_3", "Average of the last three"))


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _metrics(actual: list[float], predicted: list[float]) -> tuple[float, float]:
    errors = [abs(a - p) for a, p in zip(actual, predicted)]
    mae = sum(errors) / len(errors)
    nonzero = [(a, p) for a, p in zip(actual, predicted) if a]
    mape = 0.0 if not nonzero else sum(abs((a - p) / a) for a, p in nonzero) / len(nonzero) * 100.0
    return mae, mape


def _typical_gap(days: list[date]) -> float:
    """The gap this series normally leaves between readings."""
    gaps = sorted((b - a).days for a, b in zip(days, days[1:]))
    return float(gaps[len(gaps) // 2]) if gaps else 0.0


def _reading_at(days: list[date], target: date, slack: float) -> int | None:
    """Index of the reading closest to `target`, or None when none of them is close enough."""
    found = bisect_left(days, target)
    best: tuple[int, int] | None = None
    for index in (found - 1, found):
        if 0 <= index < len(days):
            gap = abs((days[index] - target).days)
            if best is None or gap < best[1]:
                best = (index, gap)
    return best[0] if best is not None and best[1] <= slack else None


def _comparisons(values: list[float], days: list[date], weeks: int,
                 slack: float) -> tuple[list[float], dict[str, list[float]]]:
    """What each control said this many weeks ahead, and the price that arrived.

    One origin per reading that has two readings behind it, so both controls are defined on exactly
    the same origins and their misses are comparable. A horizon no reading can resolve - four weeks
    on an annual series - yields no comparisons rather than a number built from the wrong gap.
    """
    ahead = timedelta(days=weeks * 7)
    arrived: list[float] = []
    said: dict[str, list[float]] = {key: [] for key, _ in CONTROLS}
    for origin in range(2, len(values)):
        target = _reading_at(days, days[origin] + ahead, slack)
        if target is None or target <= origin:
            continue
        arrived.append(values[target])
        said["NAIVE_LAST"].append(values[origin])
        said["MOVING_AVERAGE_3"].append(sum(values[origin - 2:origin + 1]) / 3.0)
    return arrived, said


def backtest_baselines(
    observations: Iterable[Mapping[str, Any]],
    *,
    min_points: int = 24,
    min_span_days: int = 540,
    min_sources: int = 2,
    independent_sources: int | None = None,
) -> dict:
    rows = sorted(
        [dict(r) for r in observations if r.get("observation_date") and r.get("native_price") is not None],
        key=lambda r: str(r["observation_date"]),
    )
    readiness = assess_readiness(rows, min_points=min_points, min_span_days=min_span_days,
                                 min_sources=min_sources, independent_sources=independent_sources)
    if not readiness.ready:
        return {"readiness": readiness.to_dict(), "horizon_weeks": list(HORIZON_WEEKS), "models": []}
    values = [float(r["native_price"]) for r in rows]
    days = [_day(r["observation_date"]) for r in rows]
    slack = max(_typical_gap(days), float(MATCH_SLACK_DAYS))
    models = []
    for weeks in HORIZON_WEEKS:
        arrived, said = _comparisons(values, days, weeks, slack)
        if len(arrived) < MIN_COMPARISONS:
            continue
        for key, name in CONTROLS:
            mae, mape = _metrics(arrived, said[key])
            models.append({"model": key, "name": name, "horizon_weeks": weeks,
                           "mae": mae, "mape": mape, "n": len(arrived)})
    return {"readiness": readiness.to_dict(), "horizon_weeks": list(HORIZON_WEEKS), "models": models}
