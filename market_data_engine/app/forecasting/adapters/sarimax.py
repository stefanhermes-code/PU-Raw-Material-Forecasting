"""SARIMAX with related-series regressors (conditional, Challenger).

An ARIMA(1,1,0) on the log target with the related series' log levels as exogenous regressors,
fitted by statsmodels on the history visible at the cutoff. The regressors' FUTURE values are not
known at the cutoff, so they are carried forward at their last visible level - and the output says
so in its diagnostics, because a reviewer should know the regressor contributed its level, not its
movement. (A related series with a genuinely known future belongs in `known_future`, not here.)

`statsmodels` is a native-extension library and not an engine requirement: imported only here,
only when the method runs; UNAVAILABLE without it. INELIGIBLE when no related series is visible at
the cutoff or when the aligned history is too short to fit.
"""
from __future__ import annotations

import math
import warnings

from app.forecasting.adapter import ForecastPoint, period_after, steps_for
from app.forecasting.adapters.base import ForecastContext, ForecastOutput, Ineligible, require
from app.forecasting.adapters.conditional import ConditionalAdapter, aligned_log_levels

MIN_FIT_ROWS = 30


class SARIMAXRelated(ConditionalAdapter):
    method_key = "sarimax_related"
    version = "2"   # 2: related series at their publication lag (D2, v0.5.60)

    def _run(self, context: ForecastContext) -> ForecastOutput:
        related = self.related(context)
        sm = require("statsmodels.tsa.statespace.sarimax", for_method=self.method_key, hint="pip install statsmodels")
        days, y, x = aligned_log_levels(context.history, related)
        series = context.history
        if len(y) < MIN_FIT_ROWS:
            raise Ineligible(f"needs {MIN_FIT_ROWS} aligned periods, has {len(y)}")
        if series.last_date is None or days[-1] != series.last_date:
            raise Ineligible("a related series has no value at the target's last period")
        max_steps = max(steps_for(weeks, series.frequency, series.offset_days)[0] for weeks in context.horizons)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = sm.SARIMAX(y, exog=x, order=(1, 1, 0), trend="n",
                               enforce_stationarity=False, enforce_invertibility=False)
            fit = model.fit(disp=False, maxiter=200)
            future_exog = [list(x[-1])] * max_steps
            forecast = fit.get_forecast(steps=max_steps, exog=future_exog)
            mean = [float(v) for v in forecast.predicted_mean]
            interval = forecast.conf_int(alpha=0.2)                  # 10-90
        lows = [float(row[0]) for row in interval]
        highs = [float(row[1]) for row in interval]
        points = []
        for weeks in context.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            if steps > len(mean):
                continue
            value = math.exp(mean[steps - 1])
            points.append(ForecastPoint(weeks, period_after(series.last_date, steps, series.frequency), value,
                                        {0.1: math.exp(lows[steps - 1]), 0.5: value, 0.9: math.exp(highs[steps - 1])},
                                        steps, series.frequency, exact, steps_for(weeks, series.frequency)[0]))
        return self.succeeded(context, points, {
            "related": [s.key for s in related], "fitted_periods": len(y), "order": [1, 1, 0],
            "future_regressors": "carried forward at the last visible level"})
