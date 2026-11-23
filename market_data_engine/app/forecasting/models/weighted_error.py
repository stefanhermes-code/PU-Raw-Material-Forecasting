"""Europe: a weighted average of the last three months, plus a share of last month's error.

Stefan, September 2026. Backtested month by month on the published Western Europe DDP series,
January 2014 to July 2026, one month ahead, scored from the thirteenth month on:

    chemical   weights A/B/C (A oldest)   share of error   accuracy   average error   largest miss
    Polyol     10/10/80                   100%             98.1%      EUR 48          EUR 598
    TDI        10/10/80                   100%             97.0%      EUR 91          EUR 977
    pMDI       20/20/60                    50%             97.2%      EUR 71          EUR 542

THE ARITHMETIC
--------------
    base      = wA x price(m-3) + wB x price(m-2) + wC x price(m-1)
    forecast  = base + share x error(m-1)
    error(m)  = price(m) - base(m)                     in money, never in percent

Two and three months ahead run the same arithmetic on the forecast as if it were the price.

The settings are fixed. They were chosen on the whole history and are not re-fitted per call.

Europe only. The settings were measured on European prices, and a market elsewhere is not
answered from them.

Four months is the minimum: three for the weighted average and one for the first error.
"""
from __future__ import annotations

from math import sqrt

from app.forecasting.adapter import (
    MONTHLY, ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec,
    period_after, steps_for,
)
from app.forecasting.models.naive import _empirical_quantiles

# Store code -> ((weight A, weight B, weight C), share of the error). A is the oldest month.
SETTINGS = {
    "POLYETHER_POLYOL": ((0.1, 0.1, 0.8), 1.0),
    "TDI": ((0.1, 0.1, 0.8), 1.0),
    "PMDI": ((0.2, 0.2, 0.6), 0.5),
}
MIN_MONTHS = 4


def is_europe(series_key: str) -> bool:
    """The identity key carries the market geography as its third field."""
    parts = str(series_key or "").split("|")
    return len(parts) > 2 and parts[2] == "Europe"


def path(values: list[float], weights, share: float, months: int) -> tuple[list[float], list[float]]:
    """The next `months` forecasts, and the one-month errors the method made on the history."""
    w_a, w_b, w_c = weights
    y = list(values)
    previous = 0.0
    errors: list[float] = []
    for k in range(3, len(y)):
        base = w_a * y[k - 3] + w_b * y[k - 2] + w_c * y[k - 1]
        forecast = base + share * previous
        errors.append(y[k] - forecast)
        previous = y[k] - base
    ahead: list[float] = []
    for _ in range(months):
        base = w_a * y[-3] + w_b * y[-2] + w_c * y[-1]
        forecast = base + share * previous
        ahead.append(forecast)
        previous = forecast - base
        y.append(forecast)
    return ahead, errors


class WeightedError:
    SPEC = ModelSpec(
        key="weighted_error", family="WEIGHTED_ERROR", version="1.0",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Weighted three months with error correction",
        explanation="Takes the last three months of price, gives the most recent month the most "
                    "weight, and adds part of how far that average missed last month, so the "
                    "forecast keeps up with a market that is moving.",
        supports_quantiles=True,
        note="Europe only. Settings fixed per chemical from the 2014-2026 backtest.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def _decline(self, request: ForecastRequest, reason: str) -> ForecastResult:
        return ForecastResult(self.spec, request.as_of, (), {"reason": reason})

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        settings = SETTINGS.get(str(series.chemical or "").upper())
        if not is_europe(series.key) or settings is None:
            return self._decline(request, "settings exist for Europe TDI, pMDI and polyol only")
        if series.frequency != MONTHLY:
            return self._decline(request, "monthly series only")
        if len(series.points) < MIN_MONTHS:
            return self._decline(request, f"needs {MIN_MONTHS} months, has {len(series.points)}")
        weights, share = settings

        # The anchor is the market's own latest month when the history the method reads ends
        # before it (Stefan, 22 September 2026: published series, then the card's changes). The
        # months between are run through the method and the answer is counted from the anchor.
        last = series.last_date
        anchor = (request.config or {}).get("anchor_period") or last
        gap = (anchor.year - last.year) * 12 + (anchor.month - last.month)
        gap = max(0, gap)
        wanted = {weeks: steps_for(weeks, MONTHLY, series.offset_days)
                  for weeks in request.horizons}
        # What the card is headed with: months ahead of the price, not of the bucket.
        headed = {weeks: steps_for(weeks, MONTHLY)[0] for weeks in request.horizons}
        furthest = gap + max(steps for steps, _exact in wanted.values())
        ahead, errors = path(list(series.values), weights, share, furthest)
        # Counted from the card's price: the method's own figure for the anchor month is moved onto
        # it, and every month after moves with it. Only when there are months between.
        anchor_value = (request.config or {}).get("anchor_value")
        if gap and anchor_value is not None:
            shift = float(anchor_value) - ahead[gap - 1]
            ahead = [value + shift for value in ahead]

        points = []
        for weeks, (steps, exact) in sorted(wanted.items()):
            value = ahead[gap + steps - 1]
            quantiles = _empirical_quantiles(errors, value, request.quantiles, sqrt(gap + steps))
            points.append(ForecastPoint(
                horizon_weeks=weeks, period=period_after(anchor, steps, MONTHLY), value=value,
                quantiles=quantiles, steps=steps, resolution=MONTHLY, exact=exact,
                steps_ahead=headed[weeks],
            ))
        return ForecastResult(self.spec, request.as_of, tuple(points), {
            "form": "weighted_error", "weights": list(weights), "error_share": share,
            "months": len(series.points),
        })
