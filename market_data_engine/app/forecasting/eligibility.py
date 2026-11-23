"""Which series the tournament may rank (plan, Task 4; Charlie's decisions of 19 Oct 2026, section 2).

A series is admitted to the tournament when, at the review's as-of date:

- it has the price history a forecast needs, which is `current_market.enough_history` - six months
  of readings, or twelve weeks / thirty days of dense recent collection, **and coverage is not
  required of those months**;
- it is current: its newest visible print is within one working period of the as-of date, and the
  identity is one that is still being priced (`service.PRICED_FROM_YEAR` is the live rule);
- it is a specific market identity - one chemical, one geography, one basis - which the identity key
  already guarantees, and a mirrored or duplicate identity is noted so an aggregate can discount it
  (Review Focus 5 is a Workpackage 5 concern; the flag travels from here).

Monthly SOURCE series are not rejected for being monthly: they are inputs to the feature preparation,
not targets, and this module only judges targets.

Nothing is rejected silently. Every candidate gets a verdict with the reasons, so a review can list
what it did not rank and why.

THE HISTORY RULE IS STEFAN OWN, AND IT IS NOT NEW
-------------------------------------------------
This module used to require 24 months of coverage with at most 10% of the periods missing. Stefan,
29 October 2026, on that pair: "Long time ago we already eliminated this nonsense gate ... 1 and 2
are BS." He is right, and the record shows it twice over.

**He ruled on it on 21 September 2026**, and the rule he gave is six months of readings with no
coverage requirement - `current_market.enough_history`, which the price card has used ever since.
Its docstring says so: "Coverage is not required of the months. The engine does not require it
either: a monthly series that fails the coverage test is still forecast monthly." That sentence was
false. This module did require it, so the card and the tournament disagreed by a factor of four.
`test_a_market_indication_is_a_short_history.py` even asserts "the same rule holds the forecast
back, so the card and the Forecast page never disagree", which was not true until now.

**And he eliminated it once already, on 13 October 2026** (v0.5.13): "do not exclude these from the
evaluation ... these past decisions must be evaluated continuously ... Each individual series may be
not eligible but glued together they could very well be." That was applied to the customer
catalogue - `market_selection_ui.py`, `series_curation.py`, `customer_catalogue.py` - and not here.
One rule, three copies, and the copy the review ran was the one nobody had ruled on.

WHAT THE CHANGE IS WORTH, MEASURED
----------------------------------
Against the live store on 29 October 2026, 322 series with any history:

    24 months + 10% missing (what ran)    46 qualify     498,117 fits
    those two rules simply deleted       134 qualify     658,869 fits, 50 on under six origins
    six months, coverage not required     84 qualify     653,361 fits, none under six

Stefan's rule admits 38 series the old gate refused, drops none that it admitted, and still keeps
out the 50 series carrying one or two readings - which between them hold 108 origins, so deleting
the gate outright buys 0.8% more history and a great deal of noise. The series it frees are not
marginal: ETHYLENE_GLYCOL India CIF with 143 origins, BENZENE United States CIF 143, TOLUENE
United States CIF 143, POLYETHER_POLYOL Japan CIF 138 - all thrown out by a missing-share threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from app.forecasting.adapter import DAILY, MONTHLY, WEEKLY
from app.forecasting.history import HistoricalSeries, build_history
from app.forecasting.service import PRICED_FROM_YEAR

# The one definition of "enough history to forecast", which the price card has used since Stefan
# ruled on it (21 September 2026). Imported rather than restated: a second copy here is exactly what
# put the card and the tournament four times apart.
from raw_material_forecasting.current_market import enough_history
# How far behind the as-of date the newest print may be and the series still count as current.
# v0.5.58: "current or maintainable from continuing data collection" (CR). A monthly series is
# published after its month closes - Eurostat customs about two to three months later, the price
# reports a few weeks - so a July period read in mid-October is the newest there is, not a stale
# series. Monthly allows 125 days; a series that stopped (no print for five months or more) still fails.
CURRENT_WITHIN = {DAILY: timedelta(days=14), WEEKLY: timedelta(days=28), MONTHLY: timedelta(days=125)}


@dataclass(frozen=True)
class EligibleSeries:
    identity_key: str
    chemical: str
    history: HistoricalSeries
    eligible: bool
    reasons: tuple[str, ...] = ()
    duplicate_of: str = ""                  # another identity carrying the same history, if known

    def as_dict(self) -> dict[str, Any]:
        return {"identity_key": self.identity_key, "chemical": self.chemical, "eligible": self.eligible,
                "reasons": list(self.reasons), "duplicate_of": self.duplicate_of,
                "history": self.history.as_dict()}


@dataclass(frozen=True)
class Candidate:
    """What the caller knows about one identity: its rows and whether it is still maintained."""

    identity_key: str
    chemical: str
    rows: Sequence[dict[str, Any]]
    maintained: bool = True                 # the identity is current/maintainable in the catalogue
    frequency: str | None = None            # fixed working frequency; None = best_frequency decides
    unit: str = ""


def judge(candidate: Candidate, as_of: date, *,
          minimum_months: int | None = None) -> EligibleSeries:
    """One verdict, with every reason it failed. `minimum_months` overrides the six.

    The coverage and missing-share tests are gone; the module docstring says whose decision that was
    and what it is worth. What is left is: enough history, current, still priced, maintained.
    """
    history = build_history(candidate.rows, as_of, identity_key=candidate.identity_key,
                            chemical=candidate.chemical, frequency=candidate.frequency, unit=candidate.unit)
    reasons: list[str] = []
    series = history.series
    frequency = history.frequency
    if not candidate.maintained:
        reasons.append("identity is not marked current/maintainable")
    if not series.points:
        reasons.append("no visible history at the as-of date")
        return EligibleSeries(candidate.identity_key, candidate.chemical, history, False, tuple(reasons))

    # Enough history, by the one rule: six months of readings, or twelve weeks / thirty days
    # densely collected. An interpolated period is not counted, because a filled period is not an
    # observation - that part of the old test was sound and is kept.
    interpolated = set(history.interpolated)
    observed = [day for day, _value in series.points if day not in interpolated]
    # Across a restart, for the reason this module's docstring already gives in Stefan's words:
    # "Each individual series may be not eligible but glued together they could very well be."
    # `candidate.rows` arrive from `store_series.forecast_rows`, which does the gluing.
    if not enough_history(observed, minimum_months, across_a_restart=True):
        months = len({(day.year, day.month) for day in observed})
        reasons.append(
            f"too little history to forecast: {len(observed)} readings over {months} "
            f"{'month' if months == 1 else 'months'}")
    newest = series.last_date
    if newest is not None and as_of - newest > CURRENT_WITHIN[frequency]:
        reasons.append(f"not current: newest visible period {newest.isoformat()} is "
                       f"{(as_of - newest).days} days before the as-of date")
    if newest is not None and newest.year < PRICED_FROM_YEAR and as_of.year >= PRICED_FROM_YEAR:
        reasons.append(f"no longer priced: last period {newest.isoformat()}")
    return EligibleSeries(candidate.identity_key, candidate.chemical, history, not reasons, tuple(reasons))


def eligible_series(candidates: Iterable[Candidate], as_of: date, **rules: Any) -> list[EligibleSeries]:
    """Every candidate judged; the eligible ones first. Duplicated histories are flagged, not dropped."""
    verdicts = [judge(candidate, as_of, **rules) for candidate in candidates]
    seen: dict[tuple, str] = {}
    out = []
    for verdict in verdicts:
        signature = tuple(round(v, 6) for _, v in verdict.history.series.points[-24:])
        twin = seen.get(signature, "") if signature else ""
        if twin:
            verdict = EligibleSeries(verdict.identity_key, verdict.chemical, verdict.history, verdict.eligible,
                                     verdict.reasons, duplicate_of=twin)
        elif signature:
            seen[signature] = verdict.identity_key
        out.append(verdict)
    return sorted(out, key=lambda v: (not v.eligible, v.identity_key))
