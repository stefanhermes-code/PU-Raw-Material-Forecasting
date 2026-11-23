"""The controls nobody is allowed to skip.

Carrying the last price forward is the honest floor for any price series. Markets that move on news
are close to a random walk over short horizons, and a great deal of published forecasting turns out
not to beat this. It is therefore the first thing a champion has to clear, and it stays in the
registry permanently rather than being retired once something better appears.

Uncertainty comes from the model's own history: the spread of the errors it would have made at each
horizon, widened with the square root of the horizon as a random walk's does. No distributional
assumption beyond that, and none needed.
"""
from __future__ import annotations

from math import sqrt
from statistics import median

from app.forecasting.adapter import (
    ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec, period_after, steps_for,
)


def _empirical_quantiles(errors: list[float], centre: float, quantiles, scale: float) -> dict:
    """Quantiles from the model's own past errors, not from a normal curve.

    Price changes are fat-tailed; a normal interval is too narrow in the tail that matters. Ordering
    the errors it actually made says what the model is worth without asserting a distribution.
    """
    if len(errors) < 4:
        return {}
    ordered = sorted(errors)
    out: dict[float, float] = {}
    for q in quantiles:
        position = q * (len(ordered) - 1)
        low = ordered[int(position)]
        high = ordered[min(int(position) + 1, len(ordered) - 1)]
        offset = low + (high - low) * (position - int(position))
        out[q] = centre + offset * scale
    return dict(sorted(out.items()))


class NaiveLast:
    """Tomorrow is today."""

    SPEC = ModelSpec(
        key="naive_last", family="NAIVE", version="1.0",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Today's price, repeated",
        explanation="Takes today's price and carries it forward unchanged. It is here as the bar "
                    "to clear: a method that cannot beat assuming nothing happens is not telling "
                    "you anything.",
        supports_quantiles=True, is_control=True,
        note="Carry the last observed value forward. The floor every candidate must clear.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        if not series.points:
            return ForecastResult(self.spec, request.as_of, (), {"reason": "no history"})
        last_day, last_value = series.points[-1]
        values = list(series.values)
        points = []
        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            errors = [values[i + steps] - values[i] for i in range(len(values) - steps)]
            points.append(ForecastPoint(
                horizon_weeks=weeks,
                period=period_after(last_day, steps, series.frequency),
                value=last_value,
                quantiles=_empirical_quantiles(errors, last_value, request.quantiles, 1.0),
                steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead,
            ))
        return ForecastResult(self.spec, request.as_of, tuple(points),
                              {"observations": len(values), "anchor": last_day.isoformat()})


class SeasonalNaive:
    """This period last year.

    Kept because a chemical with a genuine annual cycle - driven by heating season, construction or
    a maintenance calendar - makes the plain naive control look better than it is.
    """

    SPEC = ModelSpec(
        key="seasonal_naive", family="NAIVE", version="1.0",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Same month last year",
        explanation="Repeats whatever the price was in the same month a year ago. It suits a "
                    "product with a real season; TDI has none, which is why it comes last by a "
                    "distance.",
        supports_quantiles=True, is_control=True,
        note="The value one seasonal cycle ago. Guards against an annual pattern flattering the "
             "plain naive control.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        period = 52 if series.frequency == "W" else 12
        values = list(series.values)
        if len(values) <= period:
            # Not enough history for a cycle. Saying so beats falling back to something else and
            # reporting it under this model's name.
            return ForecastResult(self.spec, request.as_of, (),
                                  {"reason": f"needs more than {period} periods, has {len(values)}"})
        last_day = series.points[-1][0]
        points = []
        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            index = len(values) - period + (steps - 1) % period
            centre = values[index if index < len(values) else -1]
            errors = [values[i + steps] - values[i + steps - period]
                      for i in range(max(0, period - steps), len(values) - steps)]
            points.append(ForecastPoint(
                horizon_weeks=weeks,
                period=period_after(last_day, steps, series.frequency),
                value=centre,
                quantiles=_empirical_quantiles(errors, centre, request.quantiles, 1.0),
                steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead,
            ))
        return ForecastResult(self.spec, request.as_of, tuple(points),
                              {"observations": len(values), "season": period})
