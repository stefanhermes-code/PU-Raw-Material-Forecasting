"""Last month's move, carried into this month.

Every model in the registry until now looks at the LEVEL of the price. Naive carries it forward; ETS
smooths it; the feedstock regression explains it with something else's level. None of them uses the
one thing that turned out to be the strongest relationship in the longest history HTC holds: a price
that moved up last month is more likely to move up again this month.

Measured on 151 monthly points of European TDI, January 2014 to July 2026, the correlation between
this month's change and last month's change is **+0.42**. For comparison, the best anything on the
feedstock side managed was 0.18. It is an order of magnitude more signal, and it was sitting in the
series itself.

THE ARITHMETIC
--------------
One fit per horizon, at that horizon:

    (price h months ahead - price now)  =  b x (price now - price last month)

and the answer is today's price plus what that equation says the next h months will add. Nothing is
forecast in order to make the forecast: the only input is a change that has already happened.

**No constant term.** The line is forced through the origin, and that is the most important decision
in this module. With a free constant the model adds an unconditional drift, so when the momentum
turns out to be absent it does not fall back to "nothing changes" - it falls back to "the price keeps
rising at its historical average", which is a far bolder claim made by accident. That is not
hypothetical: fitted on China's 28 months the carry comes out at -0.02 with an r-squared of 0.000,
and the constant was supplying +269 CNY a month of pure trend extrapolation under a label that said
momentum.

Through the origin, b -> 0 gives back the control exactly. So the model can only lose to naive
through estimation error on b, and a loss says the momentum is not there rather than that the model
is pointed at the wrong thing. It also scored better: on European TDI, dropping the constant improved
mean error at all three horizons.

As with the feedstock regression, the band is built from the residuals AT THAT HORIZON rather than
from one-step errors widened by the square root of the horizon. Three months out, it uses the errors
it actually made three months out.

WHAT IT SCORED
--------------
European TDI, 102 out-of-sample origins, against the two controls:

                    1 month        2 months       3 months
    naive           MAE 117.5      MAE 213.5      MAE 289.1
    ETS             MAE 111.4      MAE 211.9      MAE 297.8
    this model      MAE 102.3      MAE 196.3      MAE 283.0
    wins vs naive        51%            47%            43%

**THE TWO AND THREE MONTH COLUMNS WERE MEASURED UNDER A BUG AND DO NOT STAND.** Until v0.5.47 the
forecast applied the horizon twice (see `forecast` below), so every figure above at two and three
months came off a model that over-extrapolated by two and three times. One month is unaffected -
the factor is 1 there - so MAE 102.3 and the 51% win rate are the only numbers in this table that
survive the fix.

It would be convenient if the error had cancelled here. It did not. Re-measured on the 151-point
European DDP series in the store, 102 expanding-window origins after a 48-month warm-up, same code
and both scalings off the same fit:

                    1 month        2 months       3 months
    naive           MAE 149.9      MAE 256.4      MAE 345.8
    old scaling     MAE 141.8      MAE 310.3      MAE 597.0
    this model      MAE 141.8      MAE 250.0      MAE 349.0
    median carry         0.52           1.02           1.50

The double-scaled model lost to simply carrying the last price at both longer horizons, badly so at
three months, and the fix roughly halves the three-month error. The carry does NOT collapse to zero
on this series and shrinkage does not rescue it: it comes out near 0.5, 1.0 and 1.5, which is the
horizon sitting inside the coefficient exactly as `change_pairs` intends. (The +0.02 quoted below is
the lag-3 autocorrelation of monthly changes, which is a different quantity from the coefficient on
the CUMULATIVE h-month move - a trending series can carry a small lag-3 correlation and a carry well
above one at the same time. Reading the one as the other is what made this look harmless.)

Those re-measured figures are NOT a replacement for the table above: they are in USD off the store's
converted series where the original looks to have been in EUR, the origin protocol is reconstructed
rather than the original one, and ETS is not scored. They settle the direction and the size of the
error, not the headline numbers. **The claim that this model beats both controls at two and three
months on European TDI is unverified and needs a clean re-measurement before it is repeated.**

What the original table claimed, for the one-month column that still holds: lower average error
while being nearer than naive on about half the individual months. It is right by more when it is
right and wrong more often. A buyer who cares about the size of the miss is better served; one who
counts how often the arrow pointed the right way is not.

The one-month result is where the evidence is strongest, and it agrees with the correlation: this
month's change against last month's is +0.42 at one month, +0.13 at two and +0.02 at three. The
backtest and the correlation telling the same story is worth more than either alone. Adding toluene's
own momentum as a second driver made it worse at every horizon.

WHY IT IS A CANDIDATE AND NOT THE CARD
---------------------------------------
All of that was measured on EUROPEAN TDI, where every model answered all 102 origins and the
comparison is like for like.

On CHINA it has been measured too, and it loses. Lowering the pair threshold far enough to let it be
scored at all, and marking every model on the origins they all answered: momentum 8.3% against ETS
7.0% and carrying the last price 6.9%, on four origins. Four is not a verdict - but it is the only
direct comparison the data allows, and it points the other way from Europe.

That is not a contradiction. European TDI's month-to-month carry is +0.42 and China's is -0.02. The
model works where the momentum is, and on China there is none to work with. Borrowing the European
result to justify a Chinese number is the shortcut this application exists to avoid.

So it is registered as a candidate, it runs on every forecast, and every answer it gives is written
to the vintage store beside the controls. In a few months the record answers the question on Chinese
data, from forecasts that were actually made rather than refitted afterwards. The cards stay on ETS
until it does.
"""
from __future__ import annotations

from math import sqrt

from datetime import date

from app.forecasting.adapter import (
    DAILY, WEEKLY, ForecastPoint, ForecastRequest, ForecastResult, LicenceClass, ModelSpec,
    Series, period_after, steps_for,
)
from app.forecasting.models.naive import _empirical_quantiles
from app.forecasting.models.regression import fit_ols

# The arithmetic floor, and nothing more.
#
# There used to be twenty here. I put it there, copied from a model that fits several coefficients
# and an intercept, and it does not transfer to a model that fits one coefficient and no intercept.
# It was the reason China returned nothing.
#
# A count is the wrong instrument anyway. What matters is whether the data can tell the carry apart
# from zero, and that depends on how far the price moved as much as on how many months there are -
# twenty quiet months say less than eight volatile ones. So the carry is shrunk by its own
# reliability instead: measurable carry survives, unmeasurable carry collapses to zero, and a carry
# of zero hands back the last price, which is the right answer when you know nothing. There is
# nothing left for a threshold to decide.
#
# Three is where the arithmetic stops: with two pairs and one coefficient there is one degree of
# freedom left, and with fewer there is no residual spread to measure the carry against at all.
MIN_PAIRS = 3


def periods_between(earlier: date, later: date, frequency: str) -> float:
    """How many of the series' own periods separate two readings. Never less than one."""
    if frequency == DAILY:
        return max(1.0, float(later.toordinal() - earlier.toordinal()))
    if frequency == WEEKLY:
        return max(1.0, (later.toordinal() - earlier.toordinal()) / 7.0)
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    return max(1.0, float(months))


def change_pairs(series: Series, steps: int) -> tuple[list[list[float]], list[float]]:
    """(the rate of the last move, the move over `steps` periods after it) for every reading with both.

    IN RATES, NOT IN BUCKETS
    ------------------------
    This used to require the reading immediately before and the reading exactly `steps` ahead, by
    calendar. Anything either side of a gap was thrown away. That is right when a gap means we failed
    to observe a market that kept moving - calling those periods flat would invent evidence - and
    wrong when the gap IS the data.

    Polymeric MDI is bought when a plant needs material, not on the first of the month. November
    2021, then nothing until November 2023, is not a hole in a record; it is the record. Under the
    old rule three quarters of that history contributed nothing, and the model refused to answer at
    all because August 2026 had no print.

    So a move is now measured as a rate per period and paired with the reading before it whatever the
    spacing: 600 over four months is +150 a month, the same driver as 150 over one. The target is the
    move over the next reading's interval, scaled to the `steps` the horizon asks for, so both sides
    of the fit are on the same footing however irregularly the market was observed.

    The interval lengths travel with the pair. A rate averaged over twenty-two months says less about
    next month than one measured over four weeks, and a reader is entitled to see which they have.
    """
    points = series.points
    drivers: list[list[float]] = []
    targets: list[float] = []
    spans: list[tuple[float, float]] = []
    for position in range(1, len(points) - 1):
        day, value = points[position]
        previous_day, previous_value = points[position - 1]
        ahead_day, ahead_value = points[position + 1]
        back = periods_between(previous_day, day, series.frequency)
        forward = periods_between(day, ahead_day, series.frequency)
        drivers.append([(value - previous_value) / back])
        targets.append((ahead_value - value) / forward * steps)
        spans.append((back, forward))
    return drivers, targets, spans


def shrunk_carry(carry: float, drivers, targets, steps: int) -> tuple[float, float, float]:
    """(carry after shrinkage, its standard error, its t ratio).

    The standard error of a one-coefficient, no-intercept fit is the residual spread over the square
    root of the summed squared moves. A big carry measured off tiny moves is not a finding.

    **Overlapping windows.** At two and three months the pairs share months - March's three-month
    move and April's have two months in common - so they are not independent observations and the
    plain standard error is too small. The effective count is roughly the pairs divided by the
    horizon, which inflates the standard error by the square root of the horizon. Without this the
    three-month carry looks significant when it is not.

    **The shrinkage.** t squared over one plus t squared: a carry measured many times its own error
    passes through almost untouched, one lost in its own noise goes to zero, and everything between
    is scaled by how well it is known rather than by which side of a threshold it fell.
    """
    residuals = [targets[n] - carry * drivers[n][0] for n in range(len(targets))]
    degrees = len(targets) - 1
    driver_energy = sum(row[0] * row[0] for row in drivers)
    if degrees <= 0 or driver_energy <= 0:
        return 0.0, float("inf"), 0.0
    variance = sum(value * value for value in residuals) / degrees
    standard_error = sqrt(variance / driver_energy) * sqrt(max(steps, 1))
    if standard_error <= 0:
        return carry, 0.0, float("inf")
    t_ratio = carry / standard_error
    return carry * (t_ratio * t_ratio) / (1.0 + t_ratio * t_ratio), standard_error, t_ratio



class Momentum:
    SPEC = ModelSpec(
        key="momentum", family="AR", version="1.0",
        licence="none (arithmetic)", licence_class=LicenceClass.COMMERCIAL,
        display_name="Last month's move, continued",
        explanation="Measures from history how much of a month's move has typically carried into "
                    "the months after it, then applies that share to the most recent move. Where a "
                    "market carries nothing forward, it hands back today's price.",
        supports_quantiles=True, is_control=False,
        note="Fits the next h months' change on last month's change, at each horizon, and shrinks "
             "the fitted carry by its own standard error so an unmeasurable carry collapses to "
             "zero. Nests naive, so it can only lose through estimation error.",
    )

    def __init__(self) -> None:
        self.spec = self.SPEC

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        series = request.series
        if len(series.points) < 3:
            return ForecastResult(self.spec, request.as_of, (),
                                  {"reason": f"needs at least 3 periods, has {len(series.points)}"})
        last_day, last_value = series.points[-1]
        previous_day, previous_value = series.points[-2]
        # The rate of the last move, however long it took. This used to demand that the reading
        # before the last one fell in the immediately preceding period, and returned nothing when it
        # did not - which is why China MDI produced no forecast at all: August 2026 has no print, so
        # a market with twelve months of readings was reported as having no last move.
        latest_span = periods_between(previous_day, last_day, series.frequency)
        latest_rate = (last_value - previous_value) / latest_span

        points: list[ForecastPoint] = []
        fits: dict[str, dict] = {}
        declined: dict[str, str] = {}
        for weeks in request.horizons:
            steps, exact = steps_for(weeks, series.frequency, series.offset_days)
            ahead, _ = steps_for(weeks, series.frequency)
            drivers, targets, spans = change_pairs(series, steps)
            if len(targets) < MIN_PAIRS:
                declined[str(weeks)] = (f"{len(targets)} usable month pairs at a {steps}-period "
                                        f"horizon; {MIN_PAIRS} is the arithmetic floor")
                continue
            fitted = fit_ols(drivers, targets, intercept=False)
            if fitted is None:
                declined[str(weeks)] = "the fit is singular"
                continue
            beta, r_squared = fitted
            raw_carry = beta[0]
            carry, standard_error, t_ratio = shrunk_carry(raw_carry, drivers, targets, steps)
            # The carry already carries the horizon. `change_pairs` scales every target by `steps`
            # before the fit, so the coefficient maps a rate PER PERIOD onto the whole h-step move,
            # and the move is carry x rate with nothing further applied. Multiplying by `steps`
            # again here made the forecast move grow as steps squared: on a series rising exactly
            # +100 a month it answered +100, +400 and +900 at one, two and three months instead of
            # +100, +200 and +300. On China EXW polyether polyol that turned a series trading near
            # 1,500 into a three-month forecast of 5,764 against an actual of 1,546.
            #
            # It hid for as long as it did because the one-month answer is right either way - steps
            # is 1 there - and because on European TDI, where the module was measured, the
            # three-month carry is +0.02 and shrinkage collapses it to near zero, so the model was
            # handing back the last price and had almost nothing to over-extrapolate.
            centre = last_value + carry * latest_rate
            residuals = [targets[n] - carry * drivers[n][0] for n in range(len(targets))]
            points.append(ForecastPoint(
                horizon_weeks=weeks,
                period=period_after(last_day, steps, series.frequency),
                value=centre,
                quantiles=_empirical_quantiles(residuals, centre, request.quantiles, 1.0),
                steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead,
            ))
            fits[str(weeks)] = {
                "pairs": len(targets), "carry": round(carry, 3),
                "carry_before_shrinkage": round(raw_carry, 3),
                "standard_error": round(standard_error, 3),
                "t": round(t_ratio, 2),
                "effective_pairs": round(len(targets) / max(steps, 1), 1),
                # How far apart the readings behind this fit actually were. A carry measured across
                # twenty-two-month intervals is not the same evidence as one measured month to
                # month, and the number says which this is.
                "median_interval_periods": round(sorted(back for back, _ahead in spans)[len(spans) // 2], 1)
                if spans else None,
                "latest_move_periods": round(latest_span, 1),
                "r_squared": round(r_squared, 3),
                # Said in words as well as a number, because a carry near zero means this model is
                # handing back the control's answer and a reader is entitled to know that is what
                # happened rather than inferring it from a coefficient.
                "reading": ("the carry is not measurable in this history - the answer is the "
                            "last price"
                            if abs(carry) < 0.05 else
                            f"{carry:+.0%} of last month's move carried into the next "
                            f"{steps} month{'s' if steps != 1 else ''}"),
            }

        metadata = {"latest_change": round(latest_rate * latest_span, 2),
                    "latest_rate_per_period": round(latest_rate, 2),
                    "latest_move_periods": round(latest_span, 1),
                    "fits": fits}
        if declined:
            metadata["declined"] = declined
        if not points:
            metadata["reason"] = "; ".join(f"{weeks}w: {why}" for weeks, why in declined.items())
        return ForecastResult(self.spec, request.as_of, tuple(points), metadata)
