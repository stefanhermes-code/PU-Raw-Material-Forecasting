"""What the Forecast Performance controls are, said in words a buyer can weigh.

Stefan, 31 October 2026, reading the page on Adipic Acid China EXW: *"If no forecast method is
being chosen, then explain the bottom banner text in blue"* - and then, on the measurement,
*"So what do we change?"*

Three things were wrong with how the page put it, and all three are wording rather than rule:

1. **The gate was printed first and largest, and never said what it gates.** "Data sufficiency
   gate: BLOCKED" was the opening line of a page whose own subject - the forecast record - it does
   not gate at all. 80 markets carry a forecast in force; 35 of them read BLOCKED here.
2. **The reason was printed as the rule that failed**, `independent_sources 1 < 2`, which is a
   threshold, not an answer. For 28 of those 35 markets the answer is that one publisher prices
   the market, which is a commercial fact worth knowing and not a fault in the data plumbing.
3. **The span block read as an arithmetic complaint.** Six markets are blocked on span alone, all
   of them dense Chinese daily assessments holding ~50 readings over ~50 days. Seven weeks cannot
   test a three-month call even once. That is worth saying; `span_days 45 < 540` is not.

The thresholds themselves are untouched. One publisher is the right reason to refuse to score a
market against itself, and seven weeks is the right reason to refuse a three-month verdict.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: Weeks to months, the scale the forecast record above already prints.
WEEKS_PER_MONTH = 4

COLUMNS = ("Months ahead", "Method", "Average miss", "Average miss (%)", "Tests")


def _count(readiness: Mapping[str, Any], field: str) -> int:
    try:
        return int(readiness.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def months_ahead(horizon_weeks: Any) -> int:
    """The horizon in months, rounded the way the forecast record rounds it."""
    try:
        return round(int(horizon_weeks or 0) / WEEKS_PER_MONTH)
    except (TypeError, ValueError):
        return 0


def publishers_sentence(sources: int) -> str:
    if sources <= 0:
        return ("no publisher is recorded behind this market, so there is nothing to score it "
                "against")
    if sources == 1:
        return ("only one publisher prices this market, so there is nothing to score it against - "
                "a single publisher cannot be checked by comparing it with itself")
    return (f"{sources} publishers price this market, and the controls are scored against "
            f"independent ones")


def span_sentence(span_days: int, needed: int) -> str:
    return (f"its history runs {span_days:,} days, and the controls are scored 1, 2 and 3 months "
            f"ahead - {needed:,} days is the least that gives each of those horizons enough "
            f"completed windows to average over")


def points_sentence(points: int, needed: int) -> str:
    return (f"it holds {points:,} governed observations, and {needed:,} is the least the controls "
            f"are scored on")


def why_blocked(readiness: Mapping[str, Any]) -> list[str]:
    """Each reason the controls are not scored, as a sentence rather than a failed threshold.

    Read from the reason codes `/readiness` sends - `points 9 < 24`, `span_days 45 < 540`,
    `independent_sources 1 < 2` - because those codes are the engine's own record of which rule
    stopped it, and a second opinion computed here could disagree with the gate beside it. That is
    the defect this whole page has been carrying: one rule with several copies, and the one that
    runs is the one nobody ruled on.
    """
    said: list[str] = []
    for reason in readiness.get("reasons") or []:
        code = str(reason)
        parts = code.split()
        needed = int(parts[-1]) if parts and parts[-1].lstrip("-").isdigit() else 0
        if code.startswith("independent_sources"):
            said.append(publishers_sentence(_count(readiness, "independent_sources")))
        elif code.startswith("span_days"):
            said.append(span_sentence(_count(readiness, "span_days"), needed))
        elif code.startswith("points"):
            said.append(points_sentence(_count(readiness, "points"), needed))
        else:
            said.append(code)
    return said or ["there is not enough governed history to score them"]


def blocked_banner(market: str, readiness: Mapping[str, Any]) -> str:
    """Why the controls below are empty, and that the forecast above does not wait on it."""
    reasons = why_blocked(readiness)
    joined = reasons[0] if len(reasons) == 1 else "; and ".join(
        (", ".join(reasons[:-1]), reasons[-1]))
    return (f"The controls are not scored for {market}: {joined}. "
            f"The forecast record above is unaffected - it is what this market was actually told, "
            f"scored against the prices that arrived, and nothing holds it back.")


def ready_banner(market: str, readiness: Mapping[str, Any]) -> str:
    return (f"The controls are scored for {market}: "
            f"{_count(readiness, 'points'):,} governed observations from "
            f"{_count(readiness, 'independent_sources')} independent publishers, covering "
            f"{readiness.get('first_date')} to {readiness.get('last_date')}.")


def control_rows(models: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per control per horizon, on the same months scale as the record above.

    Sorted by horizon first so the table reads down the horizons a customer is sold, rather than
    grouping by method and leaving the reader to match months across the rows themselves.
    """
    rows = [
        {"Months ahead": months_ahead(model.get("horizon_weeks")),
         "Method": str(model.get("name") or model.get("model") or "-"),
         "Average miss": round(float(model.get("mae") or 0.0)),
         "Average miss (%)": round(float(model.get("mape") or 0.0), 1),
         "Tests": int(model.get("n") or 0)}
        for model in models
    ]
    rows.sort(key=lambda row: (row["Months ahead"], row["Method"]))
    return rows


def horizons_scored(models: Iterable[Mapping[str, Any]]) -> list[int]:
    return sorted({months_ahead(model.get("horizon_weeks")) for model in models})


def _months_phrase(months: list[int]) -> str:
    words = [str(month) for month in months]
    if len(words) <= 1:
        return words[0] if words else ""
    return f"{', '.join(words[:-1])} and {words[-1]}"


def horizons_note(models: Iterable[Mapping[str, Any]],
                  published: Iterable[Any]) -> str:
    """Which published horizons this market's history could not score, said rather than left blank.

    A horizon yielding a handful of comparisons is left out of the table instead of printed from
    them. Unsaid, the gap in the table reads as an oversight; it is a fact about the series - too
    few of its readings have another reading that far ahead of them to average over.

    TDI China EXW on 31 October 2026 is the case that made this worth printing: 107 readings over
    4,684 days, irregularly spaced, scoring 63 comparisons at one month and 19 at
    two, and too few at three. So the market is sold a three-month call that its own history cannot
    check - which is exactly the sort of thing this page exists to say out loud.
    """
    scored = set(horizons_scored(models))
    wanted = sorted({months_ahead(weeks) for weeks in published})
    missing = [month for month in wanted if month and month not in scored]
    if not missing or not scored:
        return ""
    plural = "s" if len(missing) > 1 else ""
    return (f"The {_months_phrase(missing)} month{plural}-ahead call{plural} "
            f"{'are' if plural else 'is'} not scored here: too few of this market's readings have "
            f"another reading that far ahead of them. The forecast is still published at "
            f"{'those horizons' if plural else 'that horizon'}.")


def caption(basis: str = "") -> str:
    """What the two controls are, and at which horizons they were run."""
    unit = f" in {basis}" if basis else ""
    return (
        "Two reference points: carrying the last price forward, and the average of the last three. "
        "Each was run at the same 1, 2 and 3 months ahead this market's forecasts are published "
        "for, against history it had not seen, and the miss is the average gap between what it "
        f"said and the price that arrived{unit}. They are the yardstick, not a gate: a method in "
        "force that is not beating them at its own horizon is not earning its place."
    )
