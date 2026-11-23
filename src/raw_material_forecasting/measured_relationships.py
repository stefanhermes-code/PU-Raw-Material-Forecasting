"""What the prices themselves say about the chain, for the edition being written.

The route graph asserts the chemistry: toluene is nitrated to DNT, DNT is hydrogenated to TDA, TDA
is phosgenated to TDI. That is established and needs no statistics. What a buyer cannot read off a
diagram is whether the feedstock's price actually carries into the material's, by how much, and
whether it arrives early enough to be worth watching.

`correlation` has answered that question for a year and nothing in the report ever asked it.

Stefan, 11 October 2026: "I am also not prepared to let the chemical relationship go." Run against
the store that day, polyether polyol in China against propylene oxide in China came out at +0.78
over 25 overlapping months, 95% confident between +0.55 and +0.90 - and at lag zero, with lag one
collapsing to +0.21 and lag two to -0.10. That is a real result with a commercial meaning: the two
move in the same month, so propylene oxide explains polyether polyol rather than forecasting it.

So the measurement is handed to the narrative as a fact like any other, with its sample and its
interval attached, and the engine may use it or leave it. Nothing here asserts a relationship the
prices do not show, and nothing is hidden for being weak: a pair that could not be measured says
so, and a pair measured at nearly zero says that.
"""
from __future__ import annotations

from typing import Any, Sequence

from raw_material_forecasting.chemicals import display_chemical

# How the lag reads to someone deciding what to watch.
LEADS = "the feedstock moves first"
TOGETHER = "the two move in the same month"


def _plain(value: float) -> str:
    return f"{value:+.2f}"


def _strength(coefficient: float | None, low: float | None, high: float | None) -> str:
    """What the interval allows a reader to say, in words rather than in a verdict."""
    if coefficient is None:
        return "not measurable yet"
    if low is None or high is None:
        return "measured, with no interval on this few readings"
    if low > 0.5:
        return "a strong relationship"
    if low > 0.0:
        return "a positive relationship"
    if high < 0.0:
        return "an inverse relationship"
    return "no relationship the readings can establish"


def measure(series: Sequence[Any], *, chemical: str, geography: str, feedstock: str,
            correlation) -> dict[str, Any] | None:
    """One market against one of its priced feedstocks, in the same geography.

    `series` is what `correlation.build_series` produced. `correlation` is the module itself,
    passed in so this file carries no import-time cost for a caller that never measures anything.
    """
    targets = [one for one in series
               if one.chemical == chemical and one.market_geography == geography]
    drivers = [one for one in series
               if one.chemical == feedstock and one.market_geography == geography]
    if not targets or not drivers:
        return None
    # The pair with the most months in common, not the two longest series. A twelve-year target
    # and a five-week driver can be the two longest and share nothing at all, which is how this
    # first reported "0 months of overlapping history" for TDI against toluene while both were
    # sitting in the store.
    def overlap(one, other) -> int:
        return len(set(one.points) & set(other.points))

    target, driver = max(((one, other) for one in targets for other in drivers),
                         key=lambda pair: (overlap(*pair), len(pair[0].points)))
    held = correlation.correlate_pair(target, driver)
    out: dict[str, Any] = {
        "market": f"{display_chemical(chemical)} in {geography}",
        "feedstock": f"{display_chemical(feedstock)} in {geography}",
        "status": held.status,
        "months": held.overlap_months,
    }
    best = held.best
    if best is None or best.coefficient is None:
        out["reading"] = (
            f"{out['feedstock']} against {out['market']}: not measurable yet, "
            f"{held.overlap_months} month"
            f"{'' if held.overlap_months == 1 else 's'} of overlapping history.")
        return out
    interval = correlation.fisher_interval(best.coefficient, best.overlap_months)
    low, high = interval if interval else (None, None)
    out.update({
        "coefficient": round(float(best.coefficient), 2),
        "lag_months": int(best.lag_months),
        "measured_on_months": int(best.overlap_months),
        "low": None if low is None else round(float(low), 2),
        "high": None if high is None else round(float(high), 2),
        "strength": _strength(best.coefficient, low, high),
        "timing": LEADS if best.lag_months else TOGETHER,
    })
    span = (f", 95 per cent confident between {_plain(low)} and {_plain(high)}"
            if interval else "")
    when = (f" at a lag of {best.lag_months} month{'' if best.lag_months == 1 else 's'}"
            if best.lag_months else ", in the same month")
    out["reading"] = (f"{out['feedstock']} against {out['market']}: "
                      f"{_plain(best.coefficient)}{when}, on "
                      f"{best.overlap_months} months of overlapping history{span}.")
    return out


def for_the_edition(series: Sequence[Any], pairs: Sequence[tuple[str, str, str]],
                    *, correlation) -> list[dict[str, Any]]:
    """Every market-and-feedstock pair in this edition, measured.

    `pairs` is (chemical, geography, feedstock). A pair the store cannot answer is still returned,
    saying how little it has, because "we have not measured this yet" is an honest line in a report
    and silence is not.
    """
    out = []
    seen = set()
    for chemical, geography, feedstock in pairs:
        key = (chemical, geography, feedstock)
        if key in seen:
            continue
        seen.add(key)
        held = measure(series, chemical=chemical, geography=geography, feedstock=feedstock,
                       correlation=correlation)
        if held:
            out.append(held)
    return out
