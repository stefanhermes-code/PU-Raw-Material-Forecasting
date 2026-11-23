"""LightGBM on the target and its related series (conditional, Challenger).

Direct strategy: one model per horizon, trained on the history as it stood at the cutoff, with the
features a buyer would look at - the target's own last changes and the related series' last changes
at the same period starts - and the label being the change the target made `steps` periods later.
A model per horizon rather than a recursion, because a recursion feeds its own errors forward and
the twelve-week answer would be a forecast of a forecast of a forecast.

`lightgbm` is not an engine requirement and is a native library; it is imported only here, only
when the method runs, and a machine without it reports UNAVAILABLE. INELIGIBLE when no related
series is visible at the cutoff, or when the aligned history is too short to train on.
"""
from __future__ import annotations

import math

from app.forecasting.adapter import ForecastPoint, period_after, steps_for
from app.forecasting.adapters.base import ForecastContext, ForecastOutput, Ineligible, require
from app.forecasting.adapters.conditional import ConditionalAdapter, aligned_log_levels

LAGS = 3
MIN_TRAINING_ROWS = 24


def _features(y: list[float], x: list[list[float]], index: int) -> list[float]:
    """Last `LAGS` log changes of the target and of each related series, ending at `index`."""
    out = []
    for lag in range(1, LAGS + 1):
        out.append(y[index - lag + 1] - y[index - lag])
    for col in range(len(x[0])):
        for lag in range(1, LAGS + 1):
            out.append(x[index - lag + 1][col] - x[index - lag][col])
    return out


class LightGBMRelated(ConditionalAdapter):
    method_key = "lightgbm_related"
    version = "2"   # 2: related series at their publication lag (D2, v0.5.60)

    def _run(self, context: ForecastContext) -> ForecastOutput:
        related = self.related(context)
        lightgbm = require("lightgbm", for_method=self.method_key, hint="pip install lightgbm")
        days, y, x = aligned_log_levels(context.history, related)
        series = context.history
        if series.last_date is None or not days or days[-1] != series.last_date:
            raise Ineligible("a related series has no value at the target's last period")
        points: list[ForecastPoint] = []
        diagnostics: dict = {"related": [s.key for s in related], "trained_on": {}}
        for weeks in context.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            rows, labels = [], []
            for i in range(LAGS, len(y) - steps):
                rows.append(_features(y, x, i))
                labels.append(y[i + steps] - y[i])
            if len(rows) < MIN_TRAINING_ROWS:
                continue
            model = lightgbm.LGBMRegressor(n_estimators=200, learning_rate=0.05, num_leaves=7,
                                           min_child_samples=5, verbose=-1, random_state=7)
            model.fit(rows, labels)
            fitted = model.predict(rows)
            residuals = [float(label - fit) for label, fit in zip(labels, fitted)]
            change = float(model.predict([_features(y, x, len(y) - 1)])[0])
            value = math.exp(y[-1] + change)
            band = {q: math.exp(y[-1] + change + r) for q, r in
                    ((0.1, _pick(residuals, 0.1)), (0.9, _pick(residuals, 0.9)))} if len(residuals) >= 5 else {}
            points.append(ForecastPoint(weeks, period_after(series.last_date, steps, series.frequency), value,
                                        {**band, 0.5: value} if band else {}, steps, series.frequency, exact,
                                        steps_for(weeks, series.frequency)[0]))
            diagnostics["trained_on"][str(weeks)] = len(rows)
        if not points:
            raise Ineligible(f"fewer than {MIN_TRAINING_ROWS} aligned training rows at every horizon")
        return self.succeeded(context, points, diagnostics)


def _pick(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
