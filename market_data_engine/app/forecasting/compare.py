"""Rolling-origin comparison, through the same contract the page uses.

A model is worth its runtime when it beats carrying the last price forward, measured out of sample,
on the horizons the product actually asks about. Nothing else qualifies it: not an in-sample fit, not
a plausible story about feedstock pass-through, and not a chart that looks right.

So the origin is rolled forward through the history one period at a time. At each origin every model
is handed the series as it stood on that date and nothing after it - the request object refuses
otherwise - and asked the same three horizons the cards ask. What it said is kept beside what then
happened.

Three numbers come out per model per horizon, and each answers a different question:

- **MAE** - how far out it was, in the price's own unit.
- **Coverage** - how often the truth landed inside the 10-90 band. A model with a good MAE and a band
  that holds half the time is not a better model, it is a more confident one.
- **Wins against naive** - the share of origins where it was closer than carrying the price forward.
  A model can have a better average and still lose most weeks, which is worth knowing before it is
  described as an improvement.

LIKE FOR LIKE
-------------
Every model is scored on the origins where EVERY model answered, not on the ones it happened to
manage. A model may decline an origin - too little history for its own fit, a gap where it needs a
neighbouring month - and if it is then scored only on the origins it accepted, it is being marked on
a different exam from the one its rivals sat.

That is not hypothetical. On 14 September 2026 this harness reported Chinese TDI at one month as
momentum 8.3% against ETS 8.8%, which put the candidate ahead. Momentum had answered 4 origins and
ETS had answered 21. Scored on the same 4, it was momentum 8.3% against ETS 7.0% - the opposite
ranking. The number that looked like a win was a different test.

Each model's own count is reported alongside, because a model that can only answer a fifth of the
origins has told you something about itself worth knowing.

This measures. It does not decide. Whether a candidate is registered is a separate judgement made on
what this reports, and it is made once rather than re-argued each time a number moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

from app.forecasting.adapter import (
    ForecastModel, ForecastRequest, Purpose, Series, steps_for,
)
from app.forecasting.registry import ModelRegistry

# The shortest training window an origin is attempted from. ONE definition, imported from where it
# is reasoned out rather than restated here.
#
# This file held `MIN_TRAIN = 36` with a comment that argued ten months was not enough and then
# stated 36, with nothing joining the two, and no document anywhere mentions the number. Worse, it
# has no unit: it counts PERIODS, so the same 36 is three years for a monthly series and five weeks
# for a daily one, while the comment reasoned in months. `review_origins`, three files away, is also
# 36 and is a different quantity entirely - how many origins to examine per market - and that one is
# configurable with a sanity range. This one was not.
#
# What it cost, measured 29 October 2026: adipic acid China holds 45 daily readings, and an origin
# needs its training window PLUS the shortest horizon to fit inside the series - 36 + 28 = 64 days
# against 45. Zero origins, so every method came back unranked and the market got no choice at all.
# It is admitted to the review and cannot be scored by it.
#
# Stefan, 29 October 2026: "What is this 36 number that you continue to hold on to", after twice
# telling me to drop floors of this kind. `calibration.MIN_TRAIN` is 4 and is the only one of the
# two that shows its working: two pairs are needed before the data can disagree with a fit, and a
# short history tests naive, "which is a real thing to test".
#
# A method chosen on few origins is still a thin choice. That belongs in what the review REPORTS -
# the origin count travels with every result and every proposal - not in refusing to look.
from app.forecasting.calibration import MIN_TRAIN

CONTROL_KEY = "naive_last"


@dataclass
class Trial:
    """One model's answer at one origin, kept so a like-for-like set can be taken afterwards."""
    abs_error: float
    pct_error: float | None
    inside: bool | None
    beat_control: bool | None
    # Whether the forecast called the direction of the move from the origin's value - up, down or
    # flat - the way it then went. None when the origin's value is unknown. Added for the governed
    # tournament (WP2), whose tie-break is directional accuracy at equal stored-precision MAE.
    direction_right: bool | None = None


@dataclass
class Score:
    model: str
    horizon_weeks: int
    origins: int = 0
    abs_errors: list[float] = field(default_factory=list)
    pct_errors: list[float] = field(default_factory=list)
    inside: int = 0
    banded: int = 0
    wins: int = 0
    compared: int = 0
    trials: dict = field(default_factory=dict)     # origin date -> Trial

    @property
    def mae(self) -> float | None:
        return sum(self.abs_errors) / len(self.abs_errors) if self.abs_errors else None

    @property
    def mape(self) -> float | None:
        return sum(self.pct_errors) / len(self.pct_errors) if self.pct_errors else None

    @property
    def coverage(self) -> float | None:
        return self.inside / self.banded if self.banded else None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.compared if self.compared else None

    def as_dict(self) -> dict:
        return {
            "model": self.model, "horizon_weeks": self.horizon_weeks, "origins": self.origins,
            "mae": round(self.mae, 2) if self.mae is not None else None,
            "mape": round(self.mape, 2) if self.mape is not None else None,
            "coverage": round(self.coverage, 3) if self.coverage is not None else None,
            "band_tested": self.banded,
            "win_rate_vs_naive": round(self.win_rate, 3) if self.win_rate is not None else None,
            "compared_with_naive": self.compared,
        }


def _actual_at(series: Series, origin: date, steps: int) -> tuple[date, float] | None:
    """The value `steps` periods after `origin`, as the series itself records it."""
    days = series.dates
    try:
        index = days.index(origin)
    except ValueError:
        return None
    target = index + steps
    return series.points[target] if target < len(days) else None


def rolling_origin(models: Sequence[ForecastModel], series: Series,
                   covariates: Sequence[Series] = (), horizons: tuple[int, ...] = (4, 8, 12),
                   min_train: int = MIN_TRAIN) -> dict[tuple[str, int], Score]:
    """Every model, at every origin, on every horizon.

    Models are asked once per origin rather than once per horizon, because that is how the page asks
    and because a model that fits per horizon should pay that cost where a reader would incur it.
    """
    scores: dict[tuple[str, int], Score] = {}

    def score(key: str, weeks: int) -> Score:
        return scores.setdefault((key, weeks), Score(key, weeks))

    for index in range(min_train, len(series.points)):
        origin = series.points[index][0]
        window = series.up_to(origin)
        request = ForecastRequest(
            series=window, as_of=origin, horizons=horizons,
            covariates=tuple(cov.up_to(origin) for cov in covariates),
        )
        answers: dict[str, dict[int, tuple[float, float | None, float | None]]] = {}
        for model in models:
            try:
                result = model.forecast(request)
            except Exception:
                continue
            answers[model.spec.key] = {
                point.horizon_weeks: (point.value, point.quantiles.get(0.1), point.quantiles.get(0.9))
                for point in result.points
            }

        for weeks in horizons:
            steps, _exact = steps_for(weeks, series.frequency)
            landed = _actual_at(series, origin, steps)
            if landed is None:
                continue
            _day, truth = landed
            control = answers.get(CONTROL_KEY, {}).get(weeks)
            control_error = abs(truth - control[0]) if control else None
            for key, byhorizon in answers.items():
                answer = byhorizon.get(weeks)
                if answer is None:
                    continue
                value, low, high = answer
                row = score(key, weeks)
                row.origins += 1
                row.abs_errors.append(abs(truth - value))
                if truth:
                    row.pct_errors.append(abs((truth - value) / truth) * 100.0)
                if low is not None and high is not None:
                    row.banded += 1
                    if low <= truth <= high:
                        row.inside += 1
                beat = None
                if control_error is not None and key != CONTROL_KEY:
                    row.compared += 1
                    beat = abs(truth - value) < control_error
                    if beat:
                        row.wins += 1
                row.trials[origin] = Trial(
                    abs_error=abs(truth - value),
                    pct_error=(abs((truth - value) / truth) * 100.0) if truth else None,
                    inside=(None if low is None or high is None else bool(low <= truth <= high)),
                    beat_control=beat,
                    direction_right=direction_right(window.points[-1][1], value, truth),
                )
    return scores


def direction_right(origin_value: float | None, forecast: float, truth: float) -> bool | None:
    """Did the forecast call the move's direction? Flat counts as a direction of its own."""
    if origin_value is None:
        return None
    def sign(delta: float) -> int:
        return 0 if abs(delta) < 1e-9 else (1 if delta > 0 else -1)
    return sign(forecast - origin_value) == sign(truth - origin_value)


def on_common_origins(scores: dict, horizons: tuple[int, ...]) -> list[dict]:
    """Re-score every model on the origins that EVERY model answered.

    The set is the intersection, per horizon. A model that declined an origin removes it for
    everybody, which is the price of comparing them at all - and the count of what was dropped is
    reported so nobody has to guess how much of the history this cost.
    """
    out: list[dict] = []
    for weeks in horizons:
        rows = {key[0]: score for key, score in scores.items() if key[1] == weeks}
        if not rows:
            continue
        common = set.intersection(*(set(score.trials) for score in rows.values())) if rows else set()
        for model, score in sorted(rows.items()):
            trials = [score.trials[origin] for origin in sorted(common)]
            errors = [trial.abs_error for trial in trials]
            pcts = [trial.pct_error for trial in trials if trial.pct_error is not None]
            banded = [trial for trial in trials if trial.inside is not None]
            beaten = [trial for trial in trials if trial.beat_control is not None]
            called = [trial for trial in trials if trial.direction_right is not None]
            out.append({
                "model": model, "horizon_weeks": weeks,
                # What every model was marked on.
                "common_origins": len(common),
                # What this model could answer on its own. A fifth of the rest is worth knowing.
                "own_origins": score.origins,
                "mae": round(sum(errors) / len(errors), 2) if errors else None,
                "mape": round(sum(pcts) / len(pcts), 2) if pcts else None,
                "coverage": round(sum(1 for t in banded if t.inside) / len(banded), 3) if banded else None,
                "win_rate_vs_naive": (round(sum(1 for t in beaten if t.beat_control) / len(beaten), 3)
                                      if beaten else None),
                "directional_accuracy": (round(sum(1 for t in called if t.direction_right) / len(called), 3)
                                         if called else None),
            })
    return out


def compare(registry: ModelRegistry, series: Series, covariates: Sequence[Series] = (),
            horizons: tuple[int, ...] = (4, 8, 12), min_train: int = MIN_TRAIN,
            purpose: Purpose = Purpose.RESEARCH, keys: Iterable[str] | None = None,
            like_for_like: bool = True) -> list[dict]:
    """Build every eligible model and roll the origin through the series.

    RESEARCH by default: a comparison is exactly where a model that may never be shipped still has to
    be measured, and refusing to benchmark it because of its licence would hide the one thing worth
    knowing about it.

    LIKE FOR LIKE by default too: every model marked on the origins all of them answered. Pass
    `like_for_like=False` for each model's score on its own origins, which is a different and much
    less comparable thing.
    """
    chosen = list(keys) if keys is not None else [spec.key for spec in registry.candidates(purpose=purpose)]
    models = [registry.create(key, purpose=purpose) for key in chosen]
    scores = rolling_origin(models, series, covariates, horizons, min_train)
    if like_for_like:
        return on_common_origins(scores, horizons)
    return [scores[key].as_dict()
            for key in sorted(scores, key=lambda item: (item[1], item[0]))]
