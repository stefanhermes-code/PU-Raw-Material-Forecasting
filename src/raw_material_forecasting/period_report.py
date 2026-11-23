"""What happened in a period, worked out from the series before a word is written about it.

Stefan, 8 October 2026, on a monthly or quarterly report for customers to read: it has to tell the
story of the period, and it has to read as a continuum rather than a snapshot. So every fact here
is paired with the same fact for the period before it, and a market that printed nothing still has
something said about it - what it last did, when, and that nothing new was recorded since.

Nothing in this module writes prose and nothing in it reaches a model. It produces the figures; the
sentences are composed from these figures and from nothing else, so no number can appear in a report
that was not computed here. That is the same rule the import follows, for the same reason: once a
number is in front of a customer, nobody can tell a measurement from a turn of phrase.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from raw_material_forecasting.chemicals import display_chemical
from raw_material_forecasting import units
from raw_material_forecasting.market_basis import display_basis
from raw_material_forecasting.market_lines import display_packaging, market_lines

COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four"}


def _in_words(count: int) -> str:
    return COUNT_WORDS.get(count, str(count))


MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
# How far back a quiet market is worth describing before it is simply old news.
QUIET_HORIZON_PERIODS = 8
# The report's own gate, beside the Data & Sources table. Stefan, 14 October 2026: "we have the
# table for the whole and the 2 readings for the period". The table judges a series over its whole
# history; the report carries a series only where it moved through the period, and one reading
# does not move. Customs series, two to three months late, return once a second month is published.
READINGS_FOR_A_PERIOD = 2


@dataclass(frozen=True)
class Period:
    """One month or one quarter, and the one before it."""

    year: int
    index: int                  # 1-12 for a month, 1-4 for a quarter
    kind: str = "month"         # "month" or "quarter"

    @property
    def first_day(self) -> date:
        month = self.index if self.kind == "month" else (self.index - 1) * 3 + 1
        return date(self.year, month, 1)

    @property
    def last_day(self) -> date:
        months = 1 if self.kind == "month" else 3
        month = (self.index if self.kind == "month" else (self.index - 1) * 3 + 1) + months
        year = self.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1) - timedelta(days=1)

    @property
    def previous(self) -> "Period":
        if self.index > 1:
            return Period(self.year, self.index - 1, self.kind)
        return Period(self.year - 1, 12 if self.kind == "month" else 4, self.kind)

    @property
    def name(self) -> str:
        if self.kind == "month":
            return f"{MONTH_NAMES[self.index - 1]} {self.year}"
        return f"Q{self.index} {self.year}"

    def holds(self, day: str) -> bool:
        return self.first_day.isoformat() <= str(day)[:10] <= self.last_day.isoformat()


def day_in_words(day: str) -> str:
    """A date as a customer reads it. An ISO day in a report for a buyer looks like a machine."""
    try:
        return f"{date.fromisoformat(str(day)[:10]):%d %B %Y}".lstrip("0")
    except ValueError:
        return str(day)


def period_for(day: date, kind: str = "month") -> Period:
    return Period(day.year, day.month if kind == "month" else (day.month - 1) // 3 + 1, kind)


@dataclass
class MarketPeriod:
    """One market in one period, against the period before it."""

    chemical: str
    geography: str
    currency: str
    readings: int = 0
    opened: float | None = None          # first price drawn inside the period
    closed: float | None = None          # last price drawn inside the period
    high: float | None = None
    low: float | None = None
    opened_on: str = ""
    closed_on: str = ""
    # The same market, the period before. None where that period was quiet too.
    previous_change: float | None = None
    # When nothing printed in the period: what the market last did, and when.
    last_price: float | None = None
    last_day: str = ""
    periods_quiet: int = 0
    # The period this row was read over, and how many readings the one before it held. Both are
    # here because the words that scope a figure have to be decided where the evidence is, never
    # where the sentence is written.
    period: "Period | None" = None
    previous_readings: int = 0
    # The series this row is, and nothing wider. Stefan, 14 October 2026, reading the adipic acid
    # Q3 report: the Europe line put a Eurostat import price for July end to end with two regional
    # assessments for September and called the step between them a surge; Japan was an export
    # price read against nothing; Turkiye was the average of an import and an export price. One
    # row is one chemical, in one place, on one set of terms and one packaging. `geography` is then
    # the name a reader sees ("Europe (CIF)"), and `place` is the geography itself.
    place: str = ""
    terms: str = ""
    packaging: str = ""
    # A monthly series (customs unit values) is dated on the first of its month. "On 1 July 2026"
    # reads as a day's price; it is July's, and the report says so.
    monthly: bool = False

    def _when(self, day: str) -> str:
        if self.monthly:
            try:
                return f"{date.fromisoformat(str(day)[:10]):%B %Y}"
            except ValueError:
                pass
        return day_in_words(day)

    @property
    def where(self) -> str:
        """The geography itself, for anything that looks a market up rather than names it."""
        return self.place or self.geography

    @property
    def quiet(self) -> bool:
        return self.readings == 0

    def with_period(self, period: "Period | None") -> "MarketPeriod":
        """The same row, knowing which period it was read over.

        A row built by `market_period` always carries its period. One built by hand may not, and a
        row that does not know its period cannot claim to cover one, so `band` holds it at PARTIAL
        until it is told. This is the safe way to tell it.
        """
        if self.period is not None or period is None:
            return self
        return replace(self, period=period)

    @property
    def band(self) -> str:
        """How much of the period this row actually covers.

        Stefan, 10 October 2026. Toluene China was right at 12.1 per cent and wrong at "over Q3":
        the series starts on 5 September and the quarter is ninety-two days long. The figure was
        never the fault. The word attached to it was. So the band is decided here, from the
        observation dates, and the sentence takes what it is given.
        """
        if self.readings == 0:
            return "NONE"
        if self.readings == 1:
            return "SINGLE"
        if self.period is None:
            return "PARTIAL"
        length = (self.period.last_day - self.period.first_day).days + 1
        edge = max(1, round(length / 10))
        starts_by = self.period.first_day + timedelta(days=edge)
        ends_from = self.period.last_day - timedelta(days=edge)
        opened = str(self.opened_on)[:10]
        closed = str(self.closed_on)[:10]
        if opened <= starts_by.isoformat() and closed >= ends_from.isoformat():
            return "FULL"
        return "PARTIAL"

    @property
    def scope_label(self) -> str:
        """The exact words that place this row's figures in time. Nothing else may scope them."""
        band = self.band
        if band == "NONE":
            return "not priced in this edition"
        if band == "SINGLE":
            return (f"for {self._when(self.closed_on)}" if self.monthly
                    else f"on {day_in_words(self.closed_on)}")
        if band == "FULL":
            return f"over {self.period.name}" if self.period else "over the period"
        return f"between {self._when(self.opened_on)} and {self._when(self.closed_on)}"

    @property
    def window_label(self) -> str:
        """How to name the stretch the high and the low were taken from."""
        return "Within the period" if self.band == "FULL" else "Within that window"

    @property
    def comparison_label(self) -> str:
        """The preceding period, carrying how thin it is where it is thin."""
        word = "month" if (self.period and self.period.kind == "month") else "quarter"
        if self.previous_readings and self.previous_readings < 5:
            return f"the previous {word}, on {_in_words(self.previous_readings)} readings"
        return f"the previous {word}"

    @property
    def change(self) -> float | None:
        if self.opened is None or self.closed is None:
            return None
        return self.closed - self.opened

    @property
    def change_percent(self) -> float | None:
        # One reading is a price, not a movement. Counting it as "unchanged" put single readings
        # into the tiles as markets that held (Stefan's adipic acid report, 14 October 2026).
        if self.readings < 2 or not self.opened or self.change is None:
            return None
        return self.change / self.opened * 100.0

    @property
    def direction(self) -> str:
        change = self.change_percent
        if change is None:
            return "quiet"
        if change > 0.5:
            return "rose"
        if change < -0.5:
            return "fell"
        return "held"

    @property
    def against_previous(self) -> str:
        """Whether this period carried on from the last one, turned, or settled.

        The whole point of the comparison: a reader wants to know if a rise is the second in a row
        or the end of a fall, and a report that gives only this period cannot tell them.
        """
        now, before = self.change_percent, self.previous_change
        if now is None or before is None:
            return "unknown"
        if abs(now) <= 0.5 and abs(before) <= 0.5:
            return "steady"
        if abs(now) <= 0.5:
            return "settled"
        if now * before > 0:
            return "continued"
        if before == 0:
            return "began"
        return "reversed"


# The application's own code for a chemical is not always the route graph's code. The graph was
# seeded from the technical model, which splits MDI into the crude, polymeric and monomeric cuts.
ROUTE_CODE = {"PURE_MDI": "MONOMERIC_MDI"}


def route_chain(graph, chemical: str, feedstocks: Sequence[tuple[str, str]],
                *, max_steps: int = 5) -> list[tuple[str, str, bool]]:
    """How one chemical is made, from a root raw material down to the material itself.

    The graph holds every input to everything: chlorine and carbon monoxide reach pMDI in three
    steps, which is true and is not the route a buyer means by "where does my pMDI come from".
    The commercially meaningful chain is the one through the feedstock this application already
    names under that material - benzene under pMDI, toluene under TDI - so the chain is built
    through it and then extended upward to the root it comes from.

    Each step carries the name a reader would use and whether it is a market we price.
    """
    code = ROUTE_CODE.get(chemical, chemical)
    if code not in getattr(graph, "nodes", {}):
        return []

    def path_to(target: str, source: str) -> list[str]:
        for candidate in graph.upstream_candidates(target, max_priced_steps=max_steps):
            if candidate.chemical == source:
                return list(candidate.path)
        return []

    chain: list[str] = []
    for feedstock, _note in feedstocks:
        chain = path_to(code, ROUTE_CODE.get(feedstock, feedstock))
        if chain:
            break
    if not chain:
        inputs = graph.inputs_to(code)
        chain = [code] + ([inputs[0].from_chemical] if inputs else [])
    for root in ("CRUDE_OIL", "NATURAL_GAS"):
        upward = path_to(chain[-1], root)
        if upward:
            chain = chain + upward[1:]
            break
    out = []
    back = {value: key for key, value in ROUTE_CODE.items()}
    for step in list(dict.fromkeys(chain))[::-1]:
        node = graph.node(step)
        # The application's own name wins where it has one, so the last box says pMDI and not PMDI.
        ours = back.get(step, step)
        name = display_chemical(ours)
        if name == ours:
            name = (str(getattr(node, "display_name", "") or "").strip()
                    or step.replace("_", " ").title())
        out.append((step, name, bool(getattr(node, "price_tracked", False))))
    return out


@dataclass
class Outlook:
    """Where one market is expected next, and how wide the method's own record says that is.

    The band is not drawn around the model's fit. It is the width that covered `coverage` per cent
    of the forecasts this method actually made for this market at this distance, measured over
    `trials` of them. A market whose method has not been tested enough to earn a band carries no
    outlook at all, which is why the report says "where an outlook is given".
    """

    chemical: str
    geography: str
    currency: str = "USD"
    horizon_weeks: int = 4
    value: float | None = None
    low: float | None = None
    high: float | None = None
    coverage: str = "80"
    trials: int | None = None

    @property
    def has_band(self) -> bool:
        """Whether the width has been measured off this method's own past forecasts."""
        return self.low is not None and self.high is not None and bool(self.trials)

    @property
    def stands_up(self) -> bool:
        """Whether there is a forecast at all.

        This used to require the band as well, so a market whose method had not been backtested
        yet carried no outlook and vanished from the report. Stefan, 11 October 2026: "I want to
        kill this data sufficiency gate, we are sitting on eligible data and not use it because of
        this." The band is still never invented - a market without one is given its forecast and
        told plainly that the range has not been measured.
        """
        return self.value is not None


def series_label(place: str, terms: str = "", packaging: str = "") -> str:
    """What a reader calls one series: the place, then the terms and packaging it is priced on."""
    inside = [display_basis(terms)] if str(terms or "").strip() else []
    if str(packaging or "").strip():
        inside.append(display_packaging(packaging).lower())
    return f"{place} ({', '.join(inside)})" if inside else str(place)


def _drawn_points(identities, observations, *, chemical: str, geography: str,
                  terms: str | None = None, packaging: str | None = None
                  ) -> list[tuple[str, float]]:
    """Every day this market is drawn on, in order.

    With `terms` (and `packaging`) given, only that one series: the line the customer pages draw
    for that selection. Without them every line of the market is read by date, which is what the
    report did until 14 October 2026 and why it put import, export and regional prices end to end.
    That form is kept only for callers that have not named a series.
    """
    seen: dict[str, list[float]] = {}
    for line in market_lines(identities, observations, chemical=chemical, geography=geography):
        if terms is not None:
            if line.region or line.basis.upper() != str(terms).strip().upper():
                continue
            if packaging is not None and line.packaging != str(packaging or "").strip():
                continue
        for day, value in line.points:
            seen.setdefault(day, []).append(float(value))
    return [(day, sum(values) / len(values)) for day, values in sorted(seen.items())]


def market_history(identities, observations, *, chemical: str, geography: str,
                   period: Period, periods_back: int = 12, terms: str | None = None,
                   packaging: str | None = None) -> list[tuple[date, float]]:
    """The line a chart in the report draws: this period and the ones leading up to it.

    A chart that starts where the period starts shows a buyer the month and hides the run-up to it.
    How far back is the caller's choice, counted in the periods the report is written in.
    """
    first = period
    for _step in range(max(0, periods_back - 1)):
        first = first.previous
    since, until = first.first_day.isoformat(), period.last_day.isoformat()
    return [(date.fromisoformat(day[:10]), value)
            for day, value in _drawn_points(identities, observations, chemical=chemical,
                                            geography=geography, terms=terms, packaging=packaging)
            if since <= str(day)[:10] <= until]


def market_period(identities, observations, *, chemical: str, geography: str, period: Period,
                  currency: str = "USD", terms: str | None = None,
                  packaging: str | None = None) -> MarketPeriod:
    """One market read over one period, with the period before it for context.

    Given `terms`, the row is that one series and is named for it.
    """
    points = _drawn_points(identities, observations, chemical=chemical, geography=geography,
                           terms=terms, packaging=packaging)
    if terms is None:
        out = MarketPeriod(chemical=chemical, geography=geography, currency=currency, period=period)
    else:
        out = MarketPeriod(chemical=chemical, geography=series_label(geography, terms, packaging),
                           currency=currency, period=period, place=geography,
                           terms=str(terms), packaging=str(packaging or ""),
                           monthly=len(points) >= 3 and all(str(day)[8:10] == "01"
                                                             for day, _value in points))
    inside = [(day, value) for day, value in points if period.holds(day)]
    before = period.previous
    earlier = [(day, value) for day, value in points if before.holds(day)]
    out.previous_readings = len(earlier)
    if len(earlier) >= 2:
        opened, closed = earlier[0][1], earlier[-1][1]
        out.previous_change = ((closed - opened) / opened * 100.0) if opened else None
    if inside:
        out.readings = len(inside)
        out.opened, out.opened_on = inside[0][1], inside[0][0]
        out.closed, out.closed_on = inside[-1][1], inside[-1][0]
        values = [value for _day, value in inside]
        out.high, out.low = max(values), min(values)
        return out
    # Quiet. What it last did is the story, and how long ago says whether it is still worth telling.
    past = [(day, value) for day, value in points if day <= period.last_day.isoformat()]
    if past:
        out.last_day, out.last_price = past[-1][0], past[-1][1]
        out.periods_quiet = _periods_between(out.last_day, period)
    return out


def _periods_between(day: str, period: Period) -> int:
    then = date.fromisoformat(str(day)[:10])
    if period.kind == "month":
        return (period.year - then.year) * 12 + (period.index - then.month)
    return (period.year - then.year) * 4 + (period.index - ((then.month - 1) // 3 + 1))


def report_markets(identities, observations, *, chemicals: Sequence[str],
                   geographies: Sequence[str], period: Period, currency: str = "USD"
                   ) -> list[MarketPeriod]:
    """Every selected market that priced in the period, in the order they were selected.

    A market with no reading in the period is left out. Stefan, 14 October 2026: the adipic acid
    Q3 report carried Turkiye on a December 2025 figure and Saudi Arabia and South Korea in its
    title with nothing in the quarter. A report on a period is about what priced in it.
    """
    out: list[MarketPeriod] = []
    for chemical in chemicals:
        for geography in geographies:
            row = market_period(identities, observations, chemical=chemical, geography=geography,
                                period=period, currency=currency)
            if row.readings < READINGS_FOR_A_PERIOD:
                continue
            out.append(row)
    return out


def report_series(identities, observations, *, series: Iterable[tuple[str, str, str, str]],
                  period: Period, currency: str = "USD") -> list[MarketPeriod]:
    """One row per (chemical, geography, terms, packaging) that priced in the period.

    `series` is what the customer pages offer - the same eligibility and the same Yes/No - so the
    report cannot carry a series the pages would not show.
    """
    out: list[MarketPeriod] = []
    for chemical, geography, terms, packaging in series:
        row = market_period(identities, observations, chemical=chemical, geography=geography,
                            period=period, currency=currency, terms=terms, packaging=packaging)
        if row.readings >= READINGS_FOR_A_PERIOD:
            out.append(row)
    return out


def spreads(markets: Sequence[MarketPeriod]) -> list[dict[str, Any]]:
    """Where one chemical is priced in more than one selected region, how far apart they ended.

    A buyer reads this to see whether sourcing elsewhere became cheaper or dearer over the period.
    """
    out: list[dict[str, Any]] = []
    # Only like against like: an import price in one place against an export price in another is
    # not a gap a buyer can source across.
    by_chemical: dict[tuple[str, str, str], list[MarketPeriod]] = {}
    # And only rows that moved through the period: a single reading in July does not say where a
    # market ended a quarter.
    for row in markets:
        if row.closed is not None and row.readings >= 2:
            by_chemical.setdefault((row.chemical, row.terms.upper(), row.packaging), []).append(row)
    for (chemical, _terms, _packaging), rows in by_chemical.items():
        if len(rows) < 2:
            continue
        dearest = max(rows, key=lambda item: item.closed)
        cheapest = min(rows, key=lambda item: item.closed)
        gap = dearest.closed - cheapest.closed
        opened_gap = None
        if dearest.opened is not None and cheapest.opened is not None:
            opened_gap = dearest.opened - cheapest.opened
        out.append({
            "chemical": chemical, "dearest": dearest.geography, "cheapest": cheapest.geography,
            "gap": gap, "gap_opened": opened_gap,
            "widened": None if opened_gap is None else gap - opened_gap,
            "currency": dearest.currency,
        })
    return out


def _printed(value, currency: str) -> str:
    """A figure as a document shows it, so the engine copies a string instead of formatting one."""
    return "-" if value is None else units.with_unit(f"{currency} {value:,.0f}")


def fact_record(row: MarketPeriod) -> dict[str, Any]:
    """One market as the standard says a fact has to arrive: the number and the words that scope it.

    Stefan, 10 October 2026. The producer writes sentences and produces no arithmetic, so every
    figure it is allowed to use has to come across with its window, how many readings stand behind
    it, and the exact phrase that places it in time.
    """
    def money(value):
        return None if value is None else round(float(value))

    return {
        "market": f"{row.chemical} {row.geography}",
        "chemical": row.chemical,
        "geography": row.geography,
        "place": row.where,
        "terms": display_basis(row.terms) if row.terms else "",
        "currency": row.currency,
        "unit": units.MT,
        "open": money(row.opened),
        "close": money(row.closed),
        "low": money(row.low),
        "high": money(row.high),
        "open_printed": _printed(money(row.opened), row.currency),
        "close_printed": _printed(money(row.closed), row.currency),
        "range_printed": (units.with_unit(f"{row.currency} {money(row.low):,.0f} - {money(row.high):,.0f}")
                          if row.low is not None and row.high is not None else "-"),
        "move_absolute": (None if row.closed is None or row.opened is None
                          else abs(round(row.closed) - round(row.opened))),
        "move_printed": (None if row.change_percent is None
                         else f"{row.change_percent:+.1f} per cent"),
        "move_percent": None if row.change_percent is None else round(row.change_percent, 1),
        "period": row.period.name if row.period else "",
        "period_from": row.period.first_day.isoformat() if row.period else "",
        "period_to": row.period.last_day.isoformat() if row.period else "",
        "first_observation": str(row.opened_on)[:10],
        "last_observation": str(row.closed_on)[:10],
        "observations": row.readings,
        "band": row.band,
        "scope_label": row.scope_label,
        "window_label": row.window_label,
        "previous_percent": (None if row.previous_change is None
                             else round(row.previous_change, 1)),
        "previous_printed": (None if row.previous_change is None
                             else f"{row.previous_change:+.1f} per cent"),
        "previous_observations": row.previous_readings,
        "comparison_label": row.comparison_label,
    }


def fact_pack(markets: Sequence[MarketPeriod], feedstocks: Sequence[MarketPeriod] = (),
              *, period: "Period | None" = None, currency: str = "USD") -> dict[str, Any]:
    """Everything an edition is allowed to say, with the scope on every figure of it."""
    return {
        "period": period.name if period else "",
        "currency": currency,
        "unit": units.MT,
        "markets": [fact_record(row) for row in markets],
        "feedstocks": [fact_record(row) for row in feedstocks],
        "scope_labels": sorted({label for row in list(markets) + list(feedstocks)
                                for label in (row.scope_label, row.window_label,
                                              row.comparison_label) if label}),
    }


def headline(markets: Sequence[MarketPeriod]) -> dict[str, Any]:
    """The few facts an executive summary is built from."""
    moved = [row for row in markets if row.change_percent is not None]
    rose = [row for row in moved if row.direction == "rose"]
    fell = [row for row in moved if row.direction == "fell"]
    held = [row for row in moved if row.direction == "held"]
    quiet = [row for row in markets if row.quiet]
    biggest = max(moved, key=lambda row: abs(row.change_percent), default=None)
    return {
        "markets": len(markets),
        "rose": len(rose), "fell": len(fell), "held": len(held), "quiet": len(quiet),
        "biggest": biggest,
        "continued": sum(1 for row in moved if row.against_previous == "continued"),
        "reversed": sum(1 for row in moved if row.against_previous == "reversed"),
    }
