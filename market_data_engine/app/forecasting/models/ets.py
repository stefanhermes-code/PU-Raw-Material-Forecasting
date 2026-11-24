"""Exponential smoothing - the control the design names, and the one worth beating.

A moving average weights a stale observation exactly as heavily as yesterday's and lags every turn.
Exponential smoothing discounts the past geometrically, which is both closer to how a price series
behaves and genuinely hard to beat on short noisy series. That is why it is the benchmark: clearing
it means something.

Two forms, chosen by fit rather than by preference:

- **ETS(A,N,N)** - level only. A price that wanders.
- **ETS(A,Ad,N)** - level plus a damped trend. A price that is drifting, with the damping stopping
  the drift being extrapolated forever, which is the classic way a trended forecast embarrasses
  itself at twelve weeks.

Parameters are fitted by minimising one-step squared error on the training window only, over a
coarse grid. A grid rather than an optimiser because there is no scipy in this runtime, and because
on thirty observations the likelihood surface is flat enough that the third decimal of alpha is
noise pretending to be a decision.

WHERE THE MARKET IS WATCHED, THE LEVEL IS THE NEWEST PRICE (v1.2.121, 24 November 2026)
-----------------------------------------------------------------------------------------
Smoothing believes a share of each new figure, and that is right for a figure with noise in it.
Since v1.2.116 the last point of a bucketed series is the newest print, and on a market printed
several times a period (`Series.ends_on_a_watched_price`) that is not a noisy figure. It is the
price. So there the level the forecast starts from is that price, and the smoothed level is
reported beside it. The parameters are fitted exactly as before, and so is the band.

**Only where the fit chose no trend.** Built for both forms first and measured before release
(`scripts/latest_price_vs_month/watched.py`, 41 series printed weekly, 35,698 forecasts, mean miss
at one, two and three months):

                              carrying the price   as it was    from the newest price
    no trend    (4,879)       5.40  8.83  11.14    5.56  8.82  11.13    5.40  8.83  11.14
    with trend  (7,159)       4.82  8.27  10.59    5.20  8.91  11.54    5.21  8.96  11.61

Without a trend the gain is at one month - 7.74 to 6.96 after a fall of 8% or more, 14.23 to 10.37
where the method had stood 3% or more from the price - and at two and three months there is
nothing in it either way. All three horizons take the price, so the cards do not step back up
between the first month and the second for no reason a reader could be given.

With a trend, moving the level changed nothing: what loses to carrying the price there is the
trend itself, by a point at three months and by five and a half after a sharp move (20.48 against
14.89). That is a different defect and it is not touched here.

The market that prompted this is in `service.watched_to_the_newest_price`.
"""
from __future__ import annotations

from math import sqrt

from app.forecasting.adapter import (
    ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec, period_after, steps_for,
)
from app.forecasting.models.naive import _empirical_quantiles

ALPHA_GRID = [round(0.05 * n, 2) for n in range(1, 20)]          # 0.05 .. 0.95
BETA_GRID = [round(0.05 * n, 2) for n in range(1, 11)]           # 0.05 .. 0.50
PHI_GRID = [0.80, 0.90, 0.95, 0.98]
MIN_POINTS_FOR_TREND = 12


def _smooth(values, alpha, beta=None, phi=None):
    """Run the recursion, returning the fitted one-step errors and the final state."""
    level = values[0]
    trend = (values[1] - values[0]) if (beta is not None and len(values) > 1) else 0.0
    errors = []
    for value in values[1:]:
        forecast = level + (phi * trend if beta is not None else 0.0)
        errors.append(value - forecast)
        previous_level = level
        level = alpha * value + (1 - alpha) * forecast
        if beta is not None:
            trend = beta * (level - previous_level) + (1 - beta) * phi * trend
    return errors, level, trend


def _sse(values, alpha, beta=None, phi=None) -> float:
    errors, _level, _trend = _smooth(values, alpha, beta, phi)
    return sum(error * error for error in errors)


class ExponentialSmoothing:
    SPEC = ModelSpec(
        key="ets", family="ETS", version="1.1",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Recent prices, smoothed",
        explanation="Averages the recent months, with the newest counting for most and older ones "
                    "fading out. Where the price has been drifting one way it carries some of that "
                    "drift forward, held back so it cannot run away by three months. On a market "
                    "priced through the month, with no drift to carry, it starts from the newest "
                    "price.",
        supports_quantiles=True, is_control=True,
        note="ETS(A,N,N) or ETS(A,Ad,N), chosen by in-sample fit. The benchmark a candidate has to "
             "clear to be worth its runtime.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        values = list(series.values)
        if len(values) < 4:
            return ForecastResult(self.spec, request.as_of, (),
                                  {"reason": f"needs at least 4 observations, has {len(values)}"})
        last_day = series.points[-1][0]
        allow_trend = (len(values) >= MIN_POINTS_FOR_TREND
                       and bool(request.config.get("trend", True)))

        best = min(((_sse(values, alpha), alpha, None, None) for alpha in ALPHA_GRID),
                   key=lambda row: row[0])
        if allow_trend:
            trended = min(((_sse(values, alpha, beta, phi), alpha, beta, phi)
                           for alpha in ALPHA_GRID for beta in BETA_GRID for phi in PHI_GRID),
                          key=lambda row: row[0])
            # A trend has to earn its extra parameters, or every flat series acquires a drift.
            penalty = 1.0 + 2.0 / max(len(values) - 3, 1)
            if trended[0] * penalty < best[0]:
                best = trended

        _score, alpha, beta, phi = best
        errors, level, trend = _smooth(values, alpha, beta, phi)
        smoothed = level
        from_the_price = series.ends_on_a_watched_price and beta is None
        if from_the_price:
            level = values[-1]
        points = []
        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            if beta is None:
                centre = level
            else:
                damped = sum(phi ** step for step in range(1, steps + 1))
                centre = level + damped * trend
            # A random walk's uncertainty grows with the square root of the horizon. Using the
            # one-step errors and widening them says so without claiming a distribution.
            points.append(ForecastPoint(
                horizon_weeks=weeks,
                period=period_after(last_day, steps, series.frequency),
                value=centre,
                quantiles=_empirical_quantiles(errors, centre, request.quantiles, sqrt(steps)),
                steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead,
            ))
        return ForecastResult(
            self.spec, request.as_of, tuple(points),
            {"form": "ETS(A,N,N)" if beta is None else "ETS(A,Ad,N)",
             "alpha": alpha, "beta": beta, "phi": phi, "observations": len(values),
             "starts_from": "newest price" if from_the_price else "smoothed level",
             "smoothed_level": round(smoothed, 2)},
        )
