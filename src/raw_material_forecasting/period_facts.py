"""What a period did, worked out from the readings, before a word of it is written.

Stefan, 8 October 2026, commissioning the customer report: it has to read as a continuum rather
than a snapshot, so every market is measured against the period before it as well as within the one
being reported; and a market with nothing new in it gets a proper paragraph saying there were no
new developments and what the position was before, rather than a gap.

Everything in the report's prose comes from here. The writing step is given these facts and nothing
else - it never sees the series - so it cannot produce a number that was not computed, and every
figure in the text also appears in a table or on a graph in the same document.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from raw_material_forecasting.market_lines import market_lines

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
# Below this, a move is the market holding still rather than moving. A tenth of a per cent over a
# month is noise in every series we hold.
FLAT_PERCENT = 0.5


@dataclass(frozen=True)
class Period:
    """One month or one quarter, and how to step back to the one before it."""

    kind: str          # "month" or "quarter"
    year: int
    index: int         # 1-12 for a month, 1-4 for a quarter

    @property
    def label(self) -> str:
        return (f"{MONTHS[self.index - 1]} {self.year}" if self.kind == "month"
                else f"Q{self.index} {self.year}")

    @property
    def start(self) -> date:
        month = self.index if self.kind == "month" else (self.index - 1) * 3 + 1
        return date(self.year, month, 1)

    @property
    def end(self) -> date:
        months = 1 if self.kind == "month" else 3
        month = self.start.month + months
        year = self.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1)

    def holds(self, day: str) -> bool:
        return self.start.isoformat() <= str(day)[:10] < self.end.isoformat()

    def previous(self) -> "Period":
        if self.index > 1:
            return Period(self.kind, self.year, self.index - 1)
        return Period(self.kind, self.year - 1, 12 if self.kind == "month" else 4)

    @classmethod
    def month(cls, year: int, index: int) -> "Period":
        return cls("month", year, index)

    @classmethod
    def quarter(cls, year: int, index: int) -> "Period":
        return cls("quarter", year, index)


@dataclass(frozen=True)
class MarketPeriod:
    """One market over one period, with the same market over the period before it."""

    chemical: str
    geography: str
    currency: str
    period: Period
    points: tuple[tuple[str, float], ...] = ()
    before: "MarketPeriod | None" = None
    # The newest reading before this period, for a market that printed nothing inside it.
    last_known: tuple[str, float] | None = None

    @property
    def silent(self) -> bool:
        return not self.points

    @property
    def readings(self) -> int:
        return len(self.points)

    @property
    def opened(self) -> float | None:
        return self.points[0][1] if self.points else None

    @property
    def closed(self) -> float | None:
        return self.points[-1][1] if self.points else None

    @property
    def high(self) -> float | None:
        return max(value for _day, value in self.points) if self.points else None

    @property
    def low(self) -> float | None:
        return min(value for _day, value in self.points) if self.points else None

    @property
    def change(self) -> float | None:
        if self.opened is None or self.closed is None:
            return None
        return self.closed - self.opened

    @property
    def change_percent(self) -> float | None:
        if not self.opened:
            return None
        return (self.closed - self.opened) / self.opened * 100.0

    @property
    def direction(self) -> str:
        if self.silent:
            return "silent"
        move = self.change_percent or 0.0
        if abs(move) < FLAT_PERCENT:
            return "flat"
        return "up" if move > 0 else "down"

    @property
    def continuity(self) -> str:
        """How this period stands against the one before it. The continuum, in one word."""
        if self.before is None or self.before.direction in ("silent",):
            return "first"
        here, there = self.direction, self.before.direction
        if here == "silent":
            return "quiet"
        if here == "flat" and there in ("up", "down"):
            return "steadied"
        if here == there:
            return "continued"
        if here in ("up", "down") and there in ("up", "down"):
            return "reversed"
        if here in ("up", "down") and there == "flat":
            return "moved"
        return "held"


def _points(identities, observations, *, chemical: str, geography: str, period: Period
            ) -> tuple[tuple[tuple[str, float], ...], str]:
    """The drawn line for this market, cut to this period, with the currency it is kept in."""
    lines = market_lines(identities, observations, chemical=chemical, geography=geography)
    if not lines:
        return (), ""
    line = max(lines, key=lambda item: (item.points[-1][0], len(item.points)))
    inside = tuple((day, value) for day, value in line.points if period.holds(day))
    currency = (line.assessed_in[0] if getattr(line, "assessed_in", ()) else "")
    return inside, currency


def _latest_before(identities, observations, *, chemical: str, geography: str, period: Period):
    lines = market_lines(identities, observations, chemical=chemical, geography=geography)
    before = [(day, value) for line in lines for day, value in line.points
              if str(day)[:10] < period.start.isoformat()]
    return max(before) if before else None


def market_period(identities, observations, *, chemical: str, geography: str, period: Period,
                  currency: str = "", look_back: bool = True) -> MarketPeriod:
    """This market in this period, carrying the period before it.

    `look_back` stops the recursion at one step: the report compares a period with the one before
    it, and the one before that is context for the comparison rather than a third section.
    """
    points, assessed = _points(identities, observations, chemical=chemical,
                               geography=geography, period=period)
    earlier = None
    if look_back:
        earlier = market_period(identities, observations, chemical=chemical, geography=geography,
                                period=period.previous(), currency=currency, look_back=False)
    return MarketPeriod(
        chemical=chemical, geography=geography, currency=currency or assessed, period=period,
        points=points, before=earlier,
        last_known=_latest_before(identities, observations, chemical=chemical,
                                  geography=geography, period=period) if not points else None,
    )


def months_between(earlier: str, later: date) -> int:
    day = date.fromisoformat(str(earlier)[:10])
    return (later.year - day.year) * 12 + (later.month - day.month)


def spread(markets: Sequence[MarketPeriod]) -> dict[str, Any] | None:
    """Where one chemical sits apart between regions, and whether the gap widened or closed."""
    priced = [market for market in markets if market.closed is not None]
    if len(priced) < 2:
        return None
    dearest = max(priced, key=lambda market: market.closed)
    cheapest = min(priced, key=lambda market: market.closed)
    gap = dearest.closed - cheapest.closed
    was = None
    both = [market.before for market in (dearest, cheapest)]
    if all(item is not None and item.closed is not None for item in both):
        was = both[0].closed - both[1].closed
    return {"chemical": dearest.chemical, "dearest": dearest.geography,
            "cheapest": cheapest.geography, "gap": gap, "was": was,
            "widened": None if was is None else gap > was}


def biggest_mover(markets: Iterable[MarketPeriod]) -> MarketPeriod | None:
    moved = [market for market in markets if market.change_percent is not None]
    return max(moved, key=lambda market: abs(market.change_percent)) if moved else None


@dataclass
class ReportFacts:
    """Everything the document and its prose are allowed to know."""

    period: Period
    markets: list[MarketPeriod] = field(default_factory=list)
    feedstocks: list[MarketPeriod] = field(default_factory=list)
    spreads: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def priced(self) -> list[MarketPeriod]:
        return [market for market in self.markets if not market.silent]

    @property
    def silent(self) -> list[MarketPeriod]:
        return [market for market in self.markets if market.silent]

    @property
    def mover(self) -> MarketPeriod | None:
        return biggest_mover(self.markets)

    @property
    def rose(self) -> list[MarketPeriod]:
        return [market for market in self.markets if market.direction == "up"]

    @property
    def fell(self) -> list[MarketPeriod]:
        return [market for market in self.markets if market.direction == "down"]

    @property
    def held(self) -> list[MarketPeriod]:
        return [market for market in self.markets if market.direction == "flat"]


def gather(identities, observations, *, chemicals: Sequence[str], geographies: Sequence[str],
           period: Period, feedstocks: Sequence[tuple[str, str]] = (),
           events: Sequence[dict[str, Any]] = (), currency: str = "") -> ReportFacts:
    """Every market in the selection, each against the period before it."""
    markets = [market_period(identities, observations, chemical=chemical, geography=geography,
                             period=period, currency=currency)
               for chemical in chemicals for geography in geographies]
    behind = [market_period(identities, observations, chemical=chemical, geography=geography,
                            period=period, currency=currency)
              for chemical, geography in feedstocks]
    spreads = []
    for chemical in chemicals:
        found = spread([market for market in markets if market.chemical == chemical])
        if found:
            spreads.append(found)
    inside = [event for event in events if period.holds(str(event.get("date") or ""))]
    return ReportFacts(period=period, markets=markets, feedstocks=behind,
                       spreads=spreads, events=inside)
