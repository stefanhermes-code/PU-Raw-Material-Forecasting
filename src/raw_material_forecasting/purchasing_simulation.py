"""What a buyer would have saved by acting on the forecast, measured on the forecasts we made.

Stefan, 24 September 2026. The Forecast page states that its band was "measured on 74 past
forecasts". This turns that same set of past forecasts into the question a buyer actually asks:
if I had acted on them, would I be better off?

The rules are his:

  Baseline buyer    needs Q tonnes for the coming month and buys them at the price prevailing on
                    the decision date.
  Forecast buyer    holds a share S of that volume he is free to move, and acts when the forecast
                    differs from today's price by more than a threshold T. Forecast lower: he
                    defers S of the volume and pays the month's realised price. Forecast higher:
                    he buys S of NEXT month's requirement now, at today's price, instead of at
                    next month's realised price. Otherwise he does what the baseline does.

  Saving            baseline cost minus forecast-buyer cost, per decision, losses at full value.

Three properties this module has to hold, and each has a test:

**No look-ahead.** A decision reads today's price and the forecast. The realised price enters only
when the outcome is scored. Nothing about a decision changes if the realised price changes.

**One decision per target month.** The forecasts behind the band roll the origin one month at a
time over a monthly series, so each already names a different month; a buyer who recalculates
daily still buys once a month. `one_per_target_month` enforces it whatever the source, keeping the
LAST decision available before the month it names.

**A loss is a loss.** A decision that moved volume the wrong way is carried at its full cost. No
filtering of the record after the fact.

What this module does NOT do: choose the forecasts. It is handed them. Where they come from, and
whether they reproduce the band on the screen, is the caller's problem and is the whole of the
evidence question.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Sequence

# 100 tonnes so every figure divides back to a per-tonne number a buyer can carry in his head.
DEFAULT_QUANTITY = 100.0

# Interest on volume brought forward a month. Stefan, 24 September 2026: "Carrying cost = Interest
# = 6% (assumption)". An assumption, stated as one wherever it is printed, and reported beside a
# run at zero so its effect is visible rather than buried.
CARRYING_RATE_ANNUAL = 0.06

DEFER = "defer"
PULL_FORWARD = "buy_forward"
BASELINE = "baseline"


@dataclass(frozen=True)
class Forecast:
    """One past forecast and what then happened to the price it named.

    `price_now` is the price prevailing when the forecast was made - the last value the model was
    shown. `realised` is the price of the month the forecast names, on the same definition the
    backtest scores against. The two dates are months, because the series behind the band is
    monthly and a month is the unit a purchasing decision is taken in.
    """
    series: str
    decision_month: date
    target_month: date
    price_now: float
    forecast: float
    realised: float
    currency: str = ""
    band_low: float | None = None       # fraction of the forecast, e.g. -0.1251
    band_high: float | None = None


@dataclass(frozen=True)
class Decision:
    """One month's purchasing decision, and what it cost against doing nothing."""
    series: str
    decision_month: date
    target_month: date
    price_now: float
    forecast: float
    band_low: float | None
    band_high: float | None
    action: str
    realised: float
    quantity: float
    share: float
    baseline_cost: float
    forecast_cost: float
    carrying_cost: float
    saving: float
    currency: str

    @property
    def saving_per_tonne(self) -> float:
        return self.saving / self.quantity if self.quantity else 0.0

    def as_row(self) -> dict:
        row = asdict(self)
        row["decision_month"] = self.decision_month.isoformat()
        row["target_month"] = self.target_month.isoformat()
        row["saving_per_tonne"] = round(self.saving_per_tonne, 4)
        for key in ("price_now", "forecast", "realised", "baseline_cost",
                    "forecast_cost", "carrying_cost", "saving"):
            row[key] = round(float(row[key]), 4)
        return row


def months_apart(first: date, second: date) -> int:
    return (second.year - first.year) * 12 + (second.month - first.month)


def consecutive(forecasts: Iterable[Forecast]) -> tuple[list[Forecast], list[Forecast]]:
    """(usable, excluded): the forecasts whose target month is the month after the decision.

    The monthly series has gaps - months in which nothing was published. The model still steps one
    POINT ahead at such a gap, so a forecast made in July 2014 names October 2014. A buyer cannot
    defer a July purchase into October on a one-month decision, and the carrying cost of holding
    volume for three months is not the carrying cost of holding it for one. Those forecasts are
    excluded and listed rather than stretched to fit.
    """
    usable, excluded = [], []
    for item in forecasts:
        (usable if months_apart(item.decision_month, item.target_month) == 1 else excluded).append(item)
    return usable, excluded


def one_per_target_month(forecasts: Iterable[Forecast]) -> list[Forecast]:
    """One forecast per (series, target month): the last one made before that month began.

    A forecast recalculated daily produces many claims about the same month, and counting them as
    separate successes would multiply one piece of luck into thirty. The buyer decides once.
    """
    chosen: dict[tuple[str, date], Forecast] = {}
    for item in forecasts:
        key = (item.series, item.target_month)
        held = chosen.get(key)
        if held is None or item.decision_month > held.decision_month:
            chosen[key] = item
    return sorted(chosen.values(), key=lambda item: (item.series, item.target_month))


def decide(forecast: Forecast, *, threshold: float) -> str:
    """What the buyer does, from what he can see on the day and nothing else.

    `threshold` is a fraction of today's price. At zero the buyer acts on any difference; above it
    he acts only when the forecast says something big enough to be worth moving volume for.
    """
    if not forecast.price_now:
        return BASELINE
    gap = (forecast.forecast - forecast.price_now) / forecast.price_now
    if gap < -threshold:
        return DEFER
    if gap > threshold:
        return PULL_FORWARD
    return BASELINE


def score(forecast: Forecast, *, share: float, threshold: float,
          carrying_rate_annual: float = 0.0,
          quantity: float = DEFAULT_QUANTITY) -> Decision:
    """One decision, priced.

    Deferring costs nothing to carry: the volume is bought later. Bringing volume forward means
    holding it for a month, and that is where the interest assumption lands.
    """
    action = BASELINE if share <= 0 else decide(forecast, threshold=threshold)
    moved = quantity * share
    monthly_rate = carrying_rate_annual / 12.0
    baseline_cost = quantity * forecast.price_now
    carrying_cost = 0.0

    if action == DEFER:
        forecast_cost = (quantity - moved) * forecast.price_now + moved * forecast.realised
    elif action == PULL_FORWARD:
        # The extra volume is next month's requirement, so it is priced against what next month
        # would have cost. Both sides of the comparison carry this month's Q at today's price,
        # which therefore cancels; it is kept in the arithmetic so the costs read as money spent.
        carrying_cost = moved * forecast.price_now * monthly_rate
        baseline_cost = quantity * forecast.price_now + moved * forecast.realised
        forecast_cost = quantity * forecast.price_now + moved * forecast.price_now + carrying_cost
    else:
        forecast_cost = baseline_cost

    return Decision(
        series=forecast.series, decision_month=forecast.decision_month,
        target_month=forecast.target_month, price_now=forecast.price_now,
        forecast=forecast.forecast, band_low=forecast.band_low, band_high=forecast.band_high,
        action=action, realised=forecast.realised, quantity=quantity, share=share,
        baseline_cost=baseline_cost, forecast_cost=forecast_cost, carrying_cost=carrying_cost,
        saving=baseline_cost - forecast_cost, currency=forecast.currency,
    )


def simulate(forecasts: Iterable[Forecast], *, share: float, threshold: float,
             carrying_rate_annual: float = 0.0,
             quantity: float = DEFAULT_QUANTITY) -> list[Decision]:
    """Every decision, in date order, one per target month."""
    return [score(item, share=share, threshold=threshold,
                  carrying_rate_annual=carrying_rate_annual, quantity=quantity)
            for item in one_per_target_month(forecasts)]


def summarise(decisions: Sequence[Decision]) -> dict:
    """The result table for one series at one parameter set.

    `decisions_that_acted` matters as much as the average: a threshold that stops the buyer acting
    raises the average saving per ACTION while leaving him with the baseline nearly every month.
    """
    if not decisions:
        return {"decisions": 0}
    savings = [item.saving for item in decisions]
    per_tonne = [item.saving_per_tonne for item in decisions]
    acted = [item for item in decisions if item.action != BASELINE]
    best = max(decisions, key=lambda item: item.saving)
    worst = min(decisions, key=lambda item: item.saving)
    return {
        "decisions": len(decisions),
        "decisions_that_acted": len(acted),
        "average_saving_per_tonne": sum(per_tonne) / len(per_tonne),
        "total_saving": sum(savings),
        "share_that_saved": sum(1 for value in savings if value > 0) / len(savings),
        "share_that_lost": sum(1 for value in savings if value < 0) / len(savings),
        "worst_decision": worst.saving_per_tonne,
        "worst_month": worst.target_month.isoformat(),
        "best_decision": best.saving_per_tonne,
        "best_month": best.target_month.isoformat(),
        "currency": decisions[0].currency,
        "first_month": min(item.target_month for item in decisions).isoformat(),
        "last_month": max(item.target_month for item in decisions).isoformat(),
    }
