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

HALF OF THE FIRST MONTH'S TREND, AND NOTHING AFTER (v1.2.122, 24 November 2026)
---------------------------------------------------------------------------------
That different defect, measured the same day and then built. Stefan: *"Yes, measure it"*, and on
the result, *"Half, first month."*

Three tests on the 41 series printed weekly (`scripts/latest_price_vs_month/trend.py`,
`trend_weekly.py`, `trend_month_figures.py`), each fitting exactly as this module does - checked
against 35,896 of the engine's own answers, none differing. Mean miss:

                                           carrying the price    damped trend      this rule
    monthly figures ending on a print      5.06  8.48  10.80     5.31  8.86  11.38   5.11  8.45  10.76
    kept weekly, 4 / 8 / 12 weeks          4.79  8.05  10.38     4.85  8.56  11.20   4.67  7.99  10.38
    one figure a month, 1 / 3 / 5 months   4.97 10.72  14.04     5.21 11.30  14.59   5.06 10.69  13.93
    kept weekly, after a move of 8%+       8.81 12.66  15.09     9.61 16.34  20.56   8.61 12.95  15.69

The damped trend was worse than carrying the price in about 30 of the 41 markets at the longest
horizon, in both halves of the record. And the reason is in the last table of `trend_weekly.py`:
it points the right way 69% of the time at four weeks and says 3.0% where the price then moved
3.2%; at twelve weeks, 59%, 5.3% said and 2.1% moved. A move carries for about a month and then
gives some back, and a damping of 0.80 to 0.98 a step goes on adding for three.

So the drift carried is half of what the fit finds for the first four weeks, and every horizon
past that gets the same. The three cards read one figure. It is not a better forecast than
carrying the price - nothing tried was, a dozen alternatives on one sample - it is the damped
trend's cost removed with the direction kept, and the direction is the part a buyer acts on.

On a watched market the level is the newest price for this form too, now: with half a month's
drift there is nothing left for the two to disagree about (5.11 against 5.14 at one month).

Not measured: the daily markets. They had ten weeks of record on the day this was built, which is
one autumn. The weekly test is the nearest thing to them, and it is not them.
"""
from __future__ import annotations

from math import sqrt

from app.forecasting.adapter import (
    ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec, period_after, steps_for,
)
from app.forecasting.models.naive import _empirical_quantiles

# How much of the fitted trend is carried, and for how long: see the module's last section.
TREND_SHARE = 0.5
TREND_WEEKS = 4

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
        key="ets", family="ETS", version="1.2",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Recent prices, smoothed",
        explanation="Averages the recent months, with the newest counting for most and older ones "
                    "fading out. Where the price has been drifting one way it carries half of the "
                    "first month's drift forward and adds nothing after that. On a market priced "
                    "through the month it starts from the newest price.",
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
        from_the_price = series.ends_on_a_watched_price
        if from_the_price:
            level = values[-1]
        # A month of this series' own steps: one for a monthly series, four for a weekly one.
        month, _ = steps_for(TREND_WEEKS, series.frequency)
        points = []
        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            if beta is None:
                centre = level
            else:
                carried = sum(phi ** step for step in range(1, min(steps, month) + 1))
                centre = level + TREND_SHARE * carried * trend
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
             "smoothed_level": round(smoothed, 2),
             **({} if beta is None else {"trend_per_step": round(trend, 4),
                                         "trend_carried": "half of the first month"})},
        )
