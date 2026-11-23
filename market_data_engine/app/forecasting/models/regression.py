"""TDI against the things it is made from.

Every model in the registry so far reads one series and nothing else. Naive carries the last price
forward; ETS discounts the past geometrically. Neither has ever been told that TDI is made out of
toluene, and on a flexible slabstock chain where the feedstock is most of the cost, that is a large
piece of evidence sitting unused.

This is the first candidate that uses it: an ordinary least-squares fit of the price on the prices of
its feedstocks, fitted on the history and nothing else.

**Direct, not iterated.** To say where TDI will be three months out you need to know where toluene
will be three months out, and forecasting the driver to forecast the target stacks one error on
another. So the fit is done at the lag: three months ahead is a regression of TDI on the feedstock
prices of three months earlier, fitted over the whole history, and the answer for three months out is
that equation applied to today's feedstock prices. Today's prices are known. Nothing is forecast to
make the forecast. One fit per horizon, and each fit's residuals are already errors at that horizon.

That last point is worth stating plainly, because it is the one place this is better evidence than
the controls rather than merely different. Naive and ETS build their band from one-step errors and
widen it by the square root of the horizon, which assumes the series behaves like a random walk. This
model's three-month band is built from the errors it actually made three months out. It is measured
where the controls are assumed.

**Levels, not changes.** The coefficient then reads as euros of TDI per euro of toluene, which can be
argued with against what a tonne of TDI takes to make. Two trending series regressed on each other
can fit well and mean nothing, and this model does not get to assert that it has avoided that: it
clears the rolling-origin backtest against naive and ETS or it does not get released. That is the
whole reason the controls are permanent.

The model refuses rather than guesses. No covariates, too few aligned months, a covariate at a
different frequency, or a fit with fewer than ten observations per coefficient - each declines with
the reason, and the page reports a model that declined rather than showing a number built on four
points.

WHAT IT SCORED, AND WHY IT IS NOT IN THE REGISTRY
-------------------------------------------------
Measured on 14 September 2026, on European TDI - 151 unbroken monthly points from January 2014, the
longest history HTC holds - against toluene, benzene and propylene, rolling the origin through 102
out-of-sample months:

                       1 month        2 months       3 months
    naive (control)    MAE 117.5      MAE 213.5      MAE 289.1
    ETS (control)      MAE 111.4      MAE 211.9      MAE 297.8
    this model         MAE 532.4      MAE 574.7      MAE 611.6     wins 11% / 22% / 28%

Refitted in changes rather than levels - the pass-through form, which nests naive - it closes most of
the gap and still loses: 123.2 / 235.3 / 317.1, winning 34%, 37% and 40% of origins.

The reason is in the data rather than in the specification. Correlating monthly log changes at each
lead, toluene ahead of TDI by one to three months scores 0.13 to 0.18, and benzene 0.08 to 0.18 -
three per cent of the variance. Propylene's 0.36 is at lag ZERO and decays immediately: it moves WITH
TDI, not before it, which is a fact about the market and no use whatever for a forecast. European TDI
is not led by its European feedstocks at the horizons the product asks about.

The same 102 origins turned up what does work. TDI's own monthly change against its change a month
earlier is +0.42 - by a distance the strongest relationship in the pack - and a fit on that alone
beats both controls at all three horizons (MAE 102.3 / 196.3 / 283.0 against naive's 117.5 / 213.5 /
289.1). Adding toluene to it makes it worse at every horizon. That model is `momentum`, and it is
registered as a candidate.

AND AGAIN ON MDI, 19 NOVEMBER 2026
----------------------------------
The next driver worth trying arrived. Correlating monthly log changes across the whole store,
benzene South Korea leads crude polymeric MDI North America by one month at +0.61 over 83 months
(interval +0.45 to +0.73), peaking at lead 1 and decaying either side - 0.45, 0.61, 0.19, -0.09,
-0.10 - and holding in both halves of the sample (+0.63 and +0.70). Benzene Japan gives the same
shape against the same target at +0.53. That is the strongest lead relationship in the store and
much better evidence than toluene ever offered TDI.

It does not forecast. Rolling the origin through the monthly series, matched origins, this model
against the registered candidates:

                        1 month        2 months       3 months
    naive (control)     MAE  72.5      MAE 147.8      MAE 226.2
    momentum            MAE  73.1      MAE 132.0      MAE 184.9
    ets                 MAE  80.8      MAE 113.6      MAE 151.1   <- the incumbent, and the best
    this model          MAE 287.0      MAE 310.1      MAE 353.5   wins 12% / 23% / 31%

The levels form fails here the way it failed on TDI. The pass-through form was fitted too, since a
change-correlation is a claim about changes and testing levels instead would have been testing the
wrong thing: `log(target_{t+h}/target_t) = beta * log(driver_t/driver_{t-1})`, no intercept, so it
nests naive. It closes most of the gap and still loses - MAE 77.2 / 142.8 / 207.6, winning 30%,
50% and 56% of origins. At one month it is worse than carrying the price forward. At two and three
it is a coin flip against naive and 26% to 37% worse than ETS.

So the second driver fails the same gate as the first, on far better evidence, and the lesson is
the one the gate exists for: r = +0.61 on monthly changes is 37% of the variance OF THE CHANGE, and
the change is a small part of the level. A relationship can be real, stable, correctly signed and
correctly lagged, and still carry less information than the series' own last value.

One thing worth stating about the series: crude pMDI North America stopped printing on
9 August 2023. Every reading on it comes from `icis_history`, a one-off historical load rather than
a feed, so nothing lapsed - the data ends where the purchase ended. This is therefore a
retrospective result on a market no customer forecast can reach, since eligibility requires a
monthly print within 125 days. It is still the right market to have tested: it carries the
strongest lead relationship in the store and the longest history to test it on.

So this model is built, kept and benchmarked, and it is not registered. Not because of its licence -
it is arithmetic - but because it did not clear the controls, and a candidate that does not clear the
controls does not get a screen. It stays because the machinery is the point: it is the only model
that reads a second series, and the next driver worth trying - Chinese feedstocks against Chinese
TDI, freight, an outage calendar - will be tried through it. Registering it means re-running this
comparison first.
"""
from __future__ import annotations

from datetime import date

from app.forecasting.adapter import (
    ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec, Series,
    period_after, steps_for,
)
from app.forecasting.models.naive import _empirical_quantiles

# Observations per fitted coefficient. Four drivers plus an intercept on thirty months is five
# parameters on thirty points, which will fit the past beautifully and forecast nothing.
MIN_OBS_PER_PARAMETER = 10

# Below this there is no fit worth attempting whatever the number of drivers.
MIN_OBSERVATIONS = 24

# Added to the diagonal of the normal equations. Feedstock prices move together - toluene and benzene
# are both aromatics off the same reformer - so the design matrix is close to singular often enough
# that a bare solve is a liability. Small enough not to bias a well-conditioned fit.
RIDGE = 1e-6


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. None when the system is singular."""
    size = len(rhs)
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for col in range(column, size + 1):
                augmented[row][col] -= factor * augmented[column][col]
    out = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = augmented[row][size] - sum(augmented[row][col] * out[col]
                                           for col in range(row + 1, size))
        out[row] = total / augmented[row][row]
    return out


def fit_ols(rows: list[list[float]], target: list[float], *, intercept: bool = True
            ) -> tuple[list[float], float] | None:
    """Least squares. Returns (coefficients, r_squared); the intercept is first when fitted.

    `intercept=False` forces the line through the origin. That is the right choice when the model's
    whole claim is that one change drives another: a free constant adds an unconditional drift, so
    the model no longer collapses to "nothing changes" when the driver turns out to carry nothing -
    it collapses to "the price keeps rising at its historical average", which is a different and much
    bolder claim, made by accident.
    """
    design = [([1.0] if intercept else []) + row for row in rows]
    width = len(design[0])
    normal = [[sum(design[n][i] * design[n][j] for n in range(len(design))) + (RIDGE if i == j else 0.0)
               for j in range(width)] for i in range(width)]
    moment = [sum(design[n][i] * target[n] for n in range(len(design))) for i in range(width)]
    beta = _solve(normal, moment)
    if beta is None:
        return None
    mean = sum(target) / len(target) if intercept else 0.0
    total = sum((value - mean) ** 2 for value in target)
    residual = sum((target[n] - sum(beta[i] * design[n][i] for i in range(width))) ** 2
                   for n in range(len(design)))
    r_squared = 1.0 - residual / total if total else 0.0
    return beta, r_squared


def aligned(target: Series, drivers: tuple[Series, ...], lag: int
            ) -> tuple[list[date], list[list[float]], list[float]]:
    """(periods, driver rows, target values) for every period where target and every lagged driver exist.

    The lag is applied here rather than in the fit, so what comes back is already the pairing the
    regression is about: this month's TDI beside the feedstock prices of `lag` months ago.
    """
    lookup = [dict(series.points) for series in drivers]
    periods: list[date] = []
    rows: list[list[float]] = []
    values: list[float] = []
    for period, value in target.points:
        lagged = _shift(period, -lag, target.frequency)
        row = [table.get(lagged) for table in lookup]
        if any(item is None for item in row):
            continue
        periods.append(period)
        rows.append([float(item) for item in row])
        values.append(float(value))
    return periods, rows, values


def _shift(day: date, steps: int, frequency: str) -> date:
    return period_after(day, steps, frequency)


class FeedstockRegression:
    SPEC = ModelSpec(
        key="feedstock_regression", family="REGRESSION", version="1.0",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Follows the feedstock prices",
        explanation="Works out from history how the price has tracked toluene, benzene and "
                    "propylene, then puts today's feedstock prices through that relationship. It "
                    "reaches back far enough that every feedstock price it uses is already known, "
                    "so nothing has to be guessed about where toluene is going.",
        supports_quantiles=True, supports_covariates=True, is_control=False,
        note="Least squares of the price on its feedstock prices, fitted at the horizon's own lag. "
             "The first candidate that reads more than one series.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        drivers = tuple(request.covariates)
        if not drivers:
            return ForecastResult(self.spec, request.as_of, (),
                                  {"reason": "no feedstock series were supplied"})
        wrong = [d.key for d in drivers if d.frequency != series.frequency]
        if wrong:
            return ForecastResult(
                self.spec, request.as_of, (),
                {"reason": f"feedstock series are not at the frequency of the price: {', '.join(wrong)}"})
        if not series.points:
            return ForecastResult(self.spec, request.as_of, (), {"reason": "no history"})

        last_day = series.points[-1][0]
        needed = MIN_OBS_PER_PARAMETER * (len(drivers) + 1)
        points: list[ForecastPoint] = []
        fits: dict[str, dict] = {}
        declined: dict[str, str] = {}

        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            periods, rows, values = aligned(series, drivers, steps)
            if len(values) < max(MIN_OBSERVATIONS, needed):
                declined[str(weeks)] = (
                    f"{len(values)} aligned months at a {steps}-period lag; "
                    f"{max(MIN_OBSERVATIONS, needed)} are needed for {len(drivers)} feedstocks")
                continue
            fitted = fit_ols(rows, values)
            if fitted is None:
                declined[str(weeks)] = "the feedstock series are collinear and the fit is singular"
                continue
            beta, r_squared = fitted

            # Today's feedstock prices, which is what this equation is for. A driver whose latest
            # month is missing cannot answer, and saying so beats carrying a stale month forward.
            latest = [dict(driver.points).get(last_day) for driver in drivers]
            if any(item is None for item in latest):
                missing = [driver.key for driver, item in zip(drivers, latest) if item is None]
                declined[str(weeks)] = f"no reading for {', '.join(missing)} in {last_day.isoformat()}"
                continue
            centre = beta[0] + sum(beta[index + 1] * float(value) for index, value in enumerate(latest))

            # Residuals at THIS horizon. No square-root widening: these are already the errors the
            # equation made this far out, rather than one-step errors asked to stand in for them.
            residuals = [values[n] - (beta[0] + sum(beta[i + 1] * rows[n][i] for i in range(len(drivers))))
                         for n in range(len(values))]
            points.append(ForecastPoint(
                horizon_weeks=weeks,
                period=period_after(last_day, steps, series.frequency),
                value=centre,
                quantiles=_empirical_quantiles(residuals, centre, request.quantiles, 1.0),
                steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead,
            ))
            fits[str(weeks)] = {
                "lag": steps, "observations": len(values), "r_squared": round(r_squared, 3),
                "intercept": round(beta[0], 2),
                "coefficients": {driver.chemical or driver.key: round(beta[index + 1], 4)
                                 for index, driver in enumerate(drivers)},
                "first": periods[0].isoformat(), "last": periods[-1].isoformat(),
            }

        metadata = {
            "feedstocks": [driver.chemical or driver.key for driver in drivers],
            "feedstock_series": [driver.key for driver in drivers],
            "fits": fits,
        }
        if declined:
            metadata["declined"] = declined
        if not points:
            metadata["reason"] = "; ".join(f"{weeks}w: {why}" for weeks, why in declined.items()) \
                                 or "no horizon could be fitted"
        return ForecastResult(self.spec, request.as_of, tuple(points), metadata)
