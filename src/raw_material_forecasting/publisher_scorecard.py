"""A scorecard for the publishers, measured the way a buyer scores a supplier.

Stefan, 2 October 2026, on being told a score needed two publishers on the same market to work:
"I disagree with your assessment that it must be on the same market. Think about a supplier
scorecard, they do not supply the same materials and not the same volume, nor frequency.
Nevertheless I am able to create a scorecard where their perfomance is comparable in the KPI's."

He is right, and the reason is that every KPI below is a RATIO against what that publisher itself
committed to, never an absolute. A monthly assessor that printed twelve times in twelve months
scores the same as a daily collector that printed every trading day, exactly as BASF and a Thai
distributor are comparable on OTIF without selling the same material.

It also replaces something that should never have been there: a `reliability_level` of 1 to 5, set
by hand on the day a source was registered, never updated, sitting in a field that reads as
measured. Stefan: "Why do I need a handset reliability for a publisher or a source?" Nothing here is
set by hand.

Six KPIs, weighted 30/20/20/15/10/5 (Stefan went with the proposal on 2 October 2026):

  Delivery      did it publish as often as it publishes? held against owed, at its own pace
  Reachability  did our collection actually reach it? successes against attempts
  Freshness     how old is its newest reading, counted in its own periods
  Completeness  of the feeds it has ever given us, how many are still current
  Stability     the share of its feeds that are frozen: dates advancing, number not
  Agreement     where another publisher prices the same market, how far apart they sit

Agreement is the only one that needs an overlap. Where there is none it is not scored and its
weight is shared out over the rest, rather than a guess being scored as if it were a measurement.

A publisher with too little history is not scored at all. A source collected for a fortnight would
otherwise come out perfect, which is how a scorecard flatters a supplier in their first quarter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Weights, as agreed. Agreement starts low because the overlap is thin - ten markets where two
# publishers both price - and is meant to rise as the second publishers accumulate.
WEIGHTS: dict[str, int] = {
    "Delivery": 30,
    "Reachability": 20,
    "Freshness": 20,
    "Completeness": 15,
    "Stability": 10,
    "Agreement": 5,
}

# Below this there is no record to score.
MIN_DAYS = 30
MIN_READINGS = 3

# Freshness: full marks while the newest reading is within one of the publisher's own periods,
# nothing once it is four behind.
FRESH_PERIODS = 1.0
STALE_PERIODS = 4.0

# Agreement: full marks at nothing apart, nothing at a tenth apart.
AGREE_PERFECT = 0.0
AGREE_USELESS = 10.0


def _ratio(part: float, whole: float) -> float | None:
    return None if whole <= 0 else max(0.0, min(1.0, part / whole))


def _between(value: float, best: float, worst: float) -> float:
    """100 at `best`, 0 at `worst`, straight line between."""
    if worst == best:
        return 100.0
    scaled = (value - best) / (worst - best)
    return max(0.0, min(100.0, 100.0 * (1.0 - scaled)))


@dataclass(frozen=True)
class Scorecard:
    publisher: str
    scores: dict[str, float | None]
    score: float | None
    # How many feeds this publisher gives us - one market it reads, counted once
    # (`populations.FEEDS`). It was called `series`, which on this page meant four things.
    feeds: int
    readings: int
    note: str = ""

    @property
    def measured(self) -> dict[str, float]:
        return {name: value for name, value in self.scores.items() if value is not None}


def _delivery(series: list[dict]) -> float | None:
    """Held against owed, at each series' own pace, averaged over the publisher's series."""
    rates = []
    for row in series:
        owed = int(row.get("owed") or 0)
        if owed <= 0:
            continue
        rates.append(_ratio(min(int(row.get("held") or 0), owed), owed) or 0.0)
    return 100.0 * sum(rates) / len(rates) if rates else None


def _freshness(series: list[dict], *, today: date) -> float | None:
    """How many of its own periods have passed since its newest reading, at its best series."""
    ages = []
    for row in series:
        pace = float(row.get("pace_days") or 0)
        last = row.get("last")
        if pace <= 0 or not last:
            continue
        ages.append((today - date.fromisoformat(str(last)[:10])).days / pace)
    if not ages:
        return None
    return _between(min(ages), FRESH_PERIODS, STALE_PERIODS)


def _completeness(series: list[dict], *, today: date, window: int = 90) -> float | None:
    if not series:
        return None
    current = sum(1 for row in series if row.get("last")
                  and (today - date.fromisoformat(str(row["last"])[:10])).days <= window)
    return 100.0 * current / len(series)


def _stability(series: list[dict]) -> float | None:
    if not series:
        return None
    frozen = sum(1 for row in series if row.get("frozen"))
    return 100.0 * (1.0 - frozen / len(series))


def _agreement(series: list[dict]) -> float | None:
    """How far it sits from the other publisher, where there is one. Median over its series."""
    gaps = [float(row["gap_pct"]) for row in series if row.get("gap_pct") is not None]
    if not gaps:
        return None
    gaps.sort()
    median = gaps[len(gaps) // 2]
    return _between(median, AGREE_PERFECT, AGREE_USELESS)


def score_publisher(publisher: str, series: list[dict], *, today: date,
                    attempts: int = 0, reached: int = 0) -> Scorecard:
    """One publisher's scorecard. `series` is one dict per feed we take from it.

    A feed is one market read by this publisher (`populations.FEEDS`). Each carries: held, owed,
    pace_days, last, frozen, gap_pct (or None where nothing else prices that market), and readings.
    """
    readings = sum(int(row.get("readings") or 0) for row in series)
    days = 0
    firsts = [str(row.get("first"))[:10] for row in series if row.get("first")]
    if firsts:
        days = (today - date.fromisoformat(min(firsts))).days
    if readings < MIN_READINGS or days < MIN_DAYS:
        return Scorecard(publisher, {name: None for name in WEIGHTS}, None,
                         len(series), readings, note="Too new to score")

    scores: dict[str, float | None] = {
        "Delivery": _delivery(series),
        "Reachability": None if attempts <= 0 else 100.0 * (_ratio(reached, attempts) or 0.0),
        "Freshness": _freshness(series, today=today),
        "Completeness": _completeness(series, today=today),
        "Stability": _stability(series),
        "Agreement": _agreement(series),
    }
    weighted = [(WEIGHTS[name], value) for name, value in scores.items() if value is not None]
    total_weight = sum(weight for weight, _ in weighted)
    # The weight of anything that could not be measured is shared out over what could, so a
    # publisher is never marked down for a KPI nobody could compute for it.
    score = (sum(weight * value for weight, value in weighted) / total_weight) if total_weight else None
    return Scorecard(publisher, scores, None if score is None else round(score, 1),
                     len(series), readings)


def rank(cards: list[Scorecard]) -> list[Scorecard]:
    """Best first, and the ones with no record at the bottom where they belong."""
    return sorted(cards, key=lambda c: (c.score is None, -(c.score or 0.0), c.publisher))
