"""Intelligence-enhanced forecast: the engine's feature and event inputs behind the same contract.

The store already holds `feature_observation` rows (feedstock and market features, each with an
`available_from`) and `event_record` rows (announced outages, force majeure, price-change notices).
This method reads them as `context.features` - one `Series` per feature, cut at the cutoff by the
same `build_history` discipline as the target - and fits a small regression of the target's
`steps`-ahead log change on the latest log changes of each feature, with the target's own last change
as the base. Events enter as a feature series the caller builds (a count or a signed weight per
period); this adapter does not know what an event is, only that it is a series.

INELIGIBLE when no feature series is visible at the cutoff, or when fewer than MIN_ROWS periods
align. Pure Python (Windows Application Control, 25 September 2026). Deliberately simple: the
method's job in Workpackage 2 is to be a Challenger that is scored on the same exam as the rest;
what it learns from the intelligence is for the review to measure, not for this docstring to claim.
"""
from __future__ import annotations

import math
from datetime import date

from app.forecasting.adapter import ForecastPoint, Series, period_after, steps_for
from app.forecasting.adapters.base import ForecastContext, ForecastOutput, Ineligible
from app.forecasting.adapters.conditional import ConditionalAdapter, empirical_band, least_squares, at_publication_lag

MIN_ROWS = 24
MIN_FEATURE_POINTS = 12


class IntelligenceEnhanced(ConditionalAdapter):
    method_key = "intelligence_enhanced"
    version = "2"   # 2: feature series at their publication lag (D2, v0.5.60)
    needs = "feature_observation and event_record inputs for the series"

    def related(self, context: ForecastContext) -> tuple[Series, ...]:  # the inputs are features, not markets
        usable = at_publication_lag(context.history,
                                    [s for s in context.features if len(s.points) >= MIN_FEATURE_POINTS])
        if not usable:
            raise Ineligible(f"no feature or event series with {MIN_FEATURE_POINTS}+ periods visible at "
                             f"{context.cutoff.isoformat()}")
        return usable

    def _run(self, context: ForecastContext) -> ForecastOutput:
        features = self.related(context)
        series = context.history
        target = dict(series.points)
        days = [d for d, v in series.points if v > 0]
        columns = [dict(f.points) for f in features]

        def change(col: dict, day: date, previous: date) -> float | None:
            a, b = col.get(previous), col.get(day)
            if a is None or b is None:
                return None
            if a > 0 and b > 0:
                return math.log(b / a)
            return b - a                                      # a count or a signed weight: plain difference

        points: list[ForecastPoint] = []
        diagnostics: dict = {"features": [f.key for f in features], "fitted_rows": {}}
        for weeks in context.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            rows, labels = [], []
            for i in range(1, len(days) - steps):
                day, previous = days[i], days[i - 1]
                base = math.log(target[day] / target[previous])
                feats = [change(col, day, previous) for col in columns]
                if any(f is None for f in feats):
                    continue
                rows.append([base, *feats])
                labels.append(math.log(target[days[i + steps]] / target[day]))
            if len(rows) < MIN_ROWS:
                continue
            beta = least_squares(rows, labels)
            if not beta:
                continue
            last, before = days[-1], days[-2]
            feats = [change(col, last, before) for col in columns]
            if any(f is None for f in feats):
                continue                                      # a feature blank at the origin: unanswered here
            latest = [math.log(target[last] / target[before]), *feats]
            predicted = beta[0] + sum(b * f for b, f in zip(beta[1:], latest))
            residuals = [label - (beta[0] + sum(b * f for b, f in zip(beta[1:], row))) for row, label in zip(rows, labels)]
            value = target[last] * math.exp(predicted)
            band = empirical_band(residuals, predicted)
            quantiles = {q: target[last] * math.exp(v) for q, v in band.items()}
            points.append(ForecastPoint(weeks, period_after(series.last_date, steps, series.frequency), value,
                                        quantiles, steps, series.frequency, exact, steps_for(weeks, series.frequency)[0]))
            diagnostics["fitted_rows"][str(weeks)] = len(rows)
            diagnostics.setdefault("coefficients", {})[str(weeks)] = [round(b, 5) for b in beta]
        if not points:
            raise Ineligible(f"fewer than {MIN_ROWS} aligned periods with every feature at every horizon")
        return self.succeeded(context, points, diagnostics)

