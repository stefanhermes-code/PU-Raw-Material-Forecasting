"""One line per market, drawn so the reader can see what was measured and what was not.

Stefan, 26 September 2026, after going through TDI China EXW reading by reading:

  One line over the whole horizon. Green where the price was assessed by somebody else, blue from
  the day we started collecting it ourselves, and dotted green wherever the chart is joining two
  readings that are far apart - because that stretch was drawn by nobody.

  A marker on every reading. Then it is visible where the evidence actually is, rather than being
  inferred from how smooth the line looks.

The breaking rule that preceded this was wrong and worth recording. It broke a line wherever a gap
exceeded four times the line's median spacing. TDI China EXW is monthly for two and a half years
and then daily for a month; the daily end dragged the median to nothing and the rule then chopped
the monthly beginning into four pieces. A single median cannot describe a series that changes
rhythm. Markers remove the need to guess a threshold at all: a long run with no markers on it is
visibly a jump.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Container, Iterable, Sequence

from raw_material_forecasting import price_kinds, units
from raw_material_forecasting.market_basis import display_basis, stated_packaging
from .source_status import fetched_ids
from raw_material_forecasting.series_curation import (
    basis_for, is_drawn, market_key, terms_of,
)

# What a stretch of line is made of.
HISTORY = "history"      # assessed or recorded by somebody else
COLLECTED = "collected"  # read by us, from the live page, on the day
BRIDGE = "bridge"        # nothing was measured here; the chart is joining two distant readings

# Longer than this between two readings and the connection is a bridge rather than a price path.
# A monthly series steps 28 to 35 days and stays solid; two months or more is the chart drawing.
BRIDGE_DAYS = 45

# There used to be a second threshold here: a gap of a year or more was left undrawn, on the
# reasoning that even a dotted line would invite the eye to read a trend through it. Chinese pMDI
# has 608 days with nothing in them - the licensed history stops in August 2023 and the live
# collection starts in September 2026 - and the chart answered that with a blank stretch, which a
# reader takes for a market that stopped rather than a record that has a hole in it. Stefan,
# 1 October 2026: "bridge it in the graph, that is what we do with gaps." A bridge is already the
# statement that nobody measured anything in there, and it says so at any length.

# One token, decided in `units`, not restated here.
DISPLAY_UNIT = units.MT

# What the selector calls a market whose assessment is not tied to a packaging. An assessment
# with no packaging on it does not mean the material travels loose; it means the assessor did
# not distinguish, and the price covers the market whichever way it is shipped.
ANY_PACKAGING = "Any packaging"

# One copy, in market_basis, beside the other fields of the key.

# A region inside a country used to be folded into the country, so choosing China drew the national
# market and East, North and South China on top of it. Stefan, 27 September 2026, looking at TDI
# China with Drums selected: "the whole graph breaks down, non selected regions are showing".
#
# They are separate markets and the selector already offers them as such. A delivered drummed price
# in East China is assessed on different terms, in a different place, from the national ex-works
# market; drawing them together said they were one thing and then averaged nothing while showing
# four lines. A market is what the reader selected, and nothing else.
SUB_REGIONS: dict[str, tuple[str, ...]] = {}


def geographies_for(geography: str) -> tuple[str, ...]:
    """The geographies a selection draws from: the one chosen, and no other."""
    name = _text(geography)
    return (name,) + SUB_REGIONS.get(name, ())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stated(value: Any) -> str:
    """Kept as a name this module and `customer_catalogue` already import. One implementation."""
    return stated_packaging(value)


def display_packaging(packaging: str) -> str:
    """What the reader is offered for one packaging."""
    text = _stated(packaging)
    return text[:1].upper() + text[1:] if text else ANY_PACKAGING


def _day(value: Any) -> str:
    return _text(value)[:10]


def _ordinal(day: str) -> int:
    year, month, rest = day[:10].split("-")
    return date(int(year), int(month), int(rest)).toordinal()


def provenance_of(source_id: Any, collected_from: Container[str]) -> str:
    """Whether a reading came from a source we fetch, or from somebody else's assessment.

    **It takes no date, and that is the point** (masterplan G). It used to: a reading was "collected"
    if it was stamped on or after `COLLECTION_BEGAN`, the day our own collection started. That
    answered two questions with one test - did we get this, and is it recent - and got the first one
    wrong. One fetch of IMARC's page yields sixteen monthly points dated back a year, and we
    collected every one of them; the date rule called fifteen of them somebody else's history.

    Measured 28 October 2026: the date rule called **986 of 35,462** readings ours. By the source it
    is **19,214**. The rule's own justification had also expired - it cited SunSirs handing us two
    and a half years of history in one backfill, and that backfill is not in the store: the live
    `sunsirs_tdi` holds 83 readings off a page that prints its own history, and `import_sunsirs`
    holds 8. An import has its own source id, so the source separates the two cleanly.

    `collected_from` is the ids of the sources we fetch - `source_status.fetched_ids(snapshot.sources)`.
    Given nothing, nothing is ours, which is the honest answer when the caller cannot say.
    """
    return COLLECTED if _text(source_id) in (collected_from or ()) else HISTORY


@dataclass(frozen=True)
class Stroke:
    """One continuous piece of line, all of it drawn the same way."""

    kind: str
    points: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class MarketLine:
    """A market's price over the whole horizon: one set of terms, every reading we draw."""

    basis: str
    packaging: str
    points: tuple[tuple[str, float], ...]
    sources: tuple[str, ...]
    # Which kind of price this line is: assessed, contract or deal. Stefan ruled it on 27 October
    # 2026 - "Contract and spot shown separately" - and until v1.2.6 the chart averaged them into
    # one line, so a contract formula and a spot assessment of one market were a single series
    # that neither publisher had ever quoted. An ask is not a line; it never reaches here.
    kind: str = price_kinds.ASSESSED
    # The days on this line whose reading came from a source we fetch. Recorded when the line is
    # built, because that is the only place the source is still in hand - a point is (day, value)
    # and provenance is a fact about the reading, not about the day (masterplan G).
    collected_days: frozenset[str] = frozenset()
    # The terms this line is on, where a ruling has settled them. Not a grouping: two series on
    # the same terms still draw as two lines until a merging ruling says they are one market.
    terms: str = ""
    # The currencies the publishers assessed in. The chart draws everything in the reader's display
    # currency, so this is a fact about provenance rather than about the numbers on the axis, and it
    # belongs in the hover rather than in the legend.
    assessed_in: tuple[str, ...] = ()
    region: str = ""
    unit: str = DISPLAY_UNIT

    @property
    def label(self) -> str:
        """What the legend calls this line: one name for one thing.

        The basis goes through the same namer the selector above the chart uses, so a term never
        appears as "Regional assessment" in one place and REGIONAL_MARKET in the other.
        """
        parts = [display_basis(self.terms or self.basis)]
        if self.packaging:
            parts.append(display_packaging(self.packaging))
        if self.region:
            parts.append(self.region)
        # The kind is named on every line but the headline one. A market with only assessed prices
        # reads exactly as it did; one that also holds contract or deal prices says which is which.
        if self.kind and self.kind != price_kinds.HEADLINE:
            parts.append(price_kinds.display_kind(self.kind))
        return " ".join(parts)

    @property
    def first_day(self) -> str:
        return self.points[0][0] if self.points else ""

    @property
    def last_day(self) -> str:
        return self.points[-1][0] if self.points else ""

    def readings_by_provenance(self) -> dict[str, tuple[tuple[str, float], ...]]:
        """Every reading, grouped by whether we collected it or somebody else assessed it.

        Not a drawing instruction. The chart carries no markers - Stefan, 26 September 2026, looking
        at the rendered line: over the weekly years they only thicken it, and the dotted bridge
        already says where nothing was measured.

        It used to decide the headline price too, and no longer does: see `current_price` below,
        which the card reads instead. The hover still says whose reading each one is.
        """
        out: dict[str, list[tuple[str, float]]] = {HISTORY: [], COLLECTED: []}
        for day, value in self.points:
            out[self.provenance(day)].append((day, value))
        return {kind: tuple(rows) for kind, rows in out.items() if rows}

    def provenance(self, day: str) -> str:
        """Whether this line's reading for `day` is one we collected."""
        return COLLECTED if day in self.collected_days else HISTORY

    def strokes(self) -> list[Stroke]:
        """The line broken into pieces, each piece drawn one way.

        A connection between two readings more than `BRIDGE_DAYS` apart is a bridge: nobody measured
        anything in there. Shorter connections take the colour of the readings they join. However
        long the gap, it is bridged rather than left blank - a line that simply stops reads as a
        market that stopped.
        """
        points = self.points
        if len(points) < 2:
            return [Stroke(self.provenance(points[0][0]), points)] if points else []

        runs: list[Stroke] = []
        current_kind: str | None = None
        current: list[tuple[str, float]] = []

        def flush() -> None:
            if current_kind and len(current) > 1:
                runs.append(Stroke(current_kind, tuple(current)))

        for first, second in zip(points, points[1:]):
            gap = _ordinal(second[0]) - _ordinal(first[0])
            if gap > BRIDGE_DAYS:
                kind = BRIDGE
            else:
                left, right = self.provenance(first[0]), self.provenance(second[0])
                kind = COLLECTED if COLLECTED in (left, right) else HISTORY
            if kind != current_kind:
                flush()
                current_kind, current = kind, [first, second]
            else:
                current.append(second)
        flush()
        return runs


def identities_of(identities: Iterable[dict[str, Any]], *,
                  chemical: str, geography: str) -> dict[str, dict[str, Any]]:
    """The identities that make up one market, keyed by identity key.

    A market is a chemical in a geography (Stefan, 26 September 2026), and that covers more
    identities than the two words suggest: East, North and South China are regions inside China,
    and several terms can have been ruled to be one market. `market_lines` has always worked this
    out for itself; it is a function now because the PAGE needs the same answer.

    The chart no longer receives every reading in the store - it asks the engine for the readings of
    the market it is drawing, and it has to name them. Working that out in the page would be a
    second copy of this rule, and a second copy that drifted by one condition would mean a chart
    drawn from a different set of identities than the one the selectors offered.
    """
    within = set(geographies_for(geography))
    return {
        _text(row.get("identity_key")): row
        for row in identities
        if _text(row.get("product_family")) == _text(chemical)
        and _text(row.get("market_geography")) in within
        and _text(row.get("identity_key"))
    }


def market_lines(identities: Iterable[dict[str, Any]],
                 observations: Iterable[dict[str, Any]],
                 sources: Iterable[dict[str, Any]] | None = None,
                 *, chemical: str, geography: str) -> list[MarketLine]:
    """Every line this market draws.

    Readings are grouped by the terms they are read on - after any alias, so a series published as
    CFR China & Hong Kong joins the EXW China line it has been shown to be - then by packaging,
    by the currency the publisher assessed in, and by region within the country. Several publishers
    printing on the same day are averaged; that is one market read by several people. Nothing else
    is averaged.
    """
    # `sources` is back, and carries the only thing that can answer "did we collect this": which
    # sources we fetch. It was dropped when provenance became a date (`del sources`), which is the
    # defect masterplan G exists to fix.
    collected_from = fetched_ids(sources)
    wanted = identities_of(identities, chemical=chemical, geography=geography)
    if not wanted:
        return []

    buckets: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    contributors: dict[tuple[str, str, str], set[str]] = {}
    currencies: dict[tuple[str, str, str], set[str]] = {}
    settled: dict[tuple[str, str, str], set[str]] = {}
    collected: dict[tuple[str, str, str], set[str]] = {}

    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        source_id = _text(row.get("source_id"))
        if not day or not is_drawn(identity, source_id, day):
            continue
        region = _text(identity.get("market_geography"))
        assessed = (_text(row.get("source_currency")) or _text(row.get("currency"))
                    or _text(identity.get("currency")))
        # Currency is deliberately NOT part of the key. A yuan domestic assessment and a dollar
        # assessment of the same market on the same terms are one line: the snapshot has already
        # converted both into the reader's display currency, and Stefan settled on 26 September 2026
        # that CFR China & Hong Kong is the same market as EXW China. Splitting on the currency the
        # publisher happened to use would put that one market back into two lines.
        # The chemical and the geography are already chosen, so a line within this chart is the
        # market key's last two fields, refined by sub-region. Read from `market_key` rather than
        # rebuilt, so the chart cannot drift from the page that decides what a market is.
        _chemical, _place, basis, packaging = market_key(identity, source_id)
        # The kind of price is part of the line, not of the market (masterplan A, item 6). Without
        # it this bucket averaged an assessment and a recorded deal of one market into one series.
        kind = price_kinds.kind_of(identity.get("observation_type"), source_id)
        key = (basis, packaging, "" if region == _text(geography) else region, kind)
        buckets.setdefault(key, {}).setdefault(day, []).append(float(row["price"]))
        if provenance_of(source_id, collected_from) == COLLECTED:
            collected.setdefault(key, set()).add(day)
        contributors.setdefault(key, set()).add(source_id)
        currencies.setdefault(key, set()).add(assessed)
        settled.setdefault(key, set()).add(terms_of(identity, source_id))

    lines: list[MarketLine] = []
    for key, by_day in buckets.items():
        basis, packaging, region, kind = key
        points = tuple((day, sum(v) / len(v)) for day, v in sorted(by_day.items()))
        terms = sorted(settled.get(key) or ())
        lines.append(MarketLine(basis=basis, packaging=packaging, points=points, region=region,
                                kind=kind,
                                sources=tuple(sorted(contributors[key])),
                                collected_days=frozenset(collected.get(key) or ()),
                                assessed_in=tuple(sorted(currencies[key])),
                                # One set of terms per line. Where a merging ruling has already
                                # put two published terms together, the line keeps the terms it is
                                # keyed on.
                                terms=terms[0] if len(terms) == 1 else basis))
    # The headline kind first within a market, so a reader's eye lands on the assessed line.
    lines.sort(key=lambda line: (line.region, line.basis, line.packaging,
                                 price_kinds.line_order(line.kind)))
    return lines


def headline_of(lines: Iterable[MarketLine]) -> MarketLine | None:
    """The one line a market's headline price is read from.

    A market can hold up to three lines - assessed, contract, deal - and a buyer comparing contract
    against spot can only do that if one of them is the reference. The assessed line is it: a
    publisher's view of the market, which is what every other line is compared against. Where a
    market has no assessed line the first by `price_kinds.line_order` stands in, so a market with
    only recorded deals still has a headline rather than none.
    """
    ordered = sorted(lines, key=lambda line: price_kinds.line_order(line.kind))
    return ordered[0] if ordered else None


def kinds_drawn(lines: Iterable[MarketLine]) -> tuple[str, ...]:
    """The kinds of price this market has lines for, headline first."""
    return tuple(sorted({line.kind for line in lines}, key=price_kinds.line_order))


def lines_on(lines: Iterable[MarketLine], basis: str) -> list[MarketLine]:
    """The lines drawn on one set of terms.

    Stefan, 27 September 2026, on TDI Europe: five markets on one pair of axes - a five-print spot
    CFR, a purchase record that ends in 2017, twelve years of a published DDP series, 440 weeks of
    contract FD, and a five-point regional index, together spanning 1,596 to 4,769 USD. The
    Incoterm selector sat above all of it and only chose which one the headline was read from.

    It was drawn that way because a market is a chemical in a geography and the Incoterm is a choice
    inside it (his ruling, 26 September). That is still true of the market. It is not a reason to
    draw every choice at once: the reader makes the choice, and the chart shows what was chosen.
    """
    wanted = _text(basis).upper()
    return [line for line in lines if line.basis.upper() == wanted]


def packaging_choices(lines: Iterable[MarketLine]) -> tuple[str, ...]:
    """The packagings a market is assessed in, in the order the selector offers them.

    Stefan, 26 September 2026: packaging goes back in the selector. TDI China carries the market's
    own assessment, which names no packaging, and the domestic regional assessments, which are
    drummed; drawing both at once put two different things on one chart and left the reader to
    tell them apart by eye. It is a choice, so it belongs above the chart.

    The assessment that names no packaging comes first, because it is the market itself.
    """
    return tuple(sorted({line.packaging for line in lines}, key=lambda name: (name != "", name.lower())))


def lines_packaged(lines: Iterable[MarketLine], packaging: str) -> list[MarketLine]:
    """The lines drawn for one packaging."""
    wanted = _stated(packaging)
    return [line for line in lines if line.packaging == wanted]


def opening_packaging(lines: Iterable[MarketLine], basis: str, offered: Sequence[str]) -> str:
    """The packaging Current Market opens a set of terms on: the first one priced now.

    Moved here from the page on 24 November 2026 so the Forecast page can ask the same question
    and get the same answer. See `headline_price`.
    """
    wanted = _text(basis).upper()
    offered = tuple(offered) or ("",)
    return next((line.packaging for line in lines
                 if line.basis.upper() == wanted and current_price(line)), offered[0])


def headline_price(lines: Iterable[MarketLine], basis: str, packaging: str,
                   *, as_of: date | None = None) -> tuple[str, float] | None:
    """The day and the figure the Current Market card prints for one selection, or nothing.

    ONE PLACE, BECAUSE TWO PAGES PRINT IT
    --------------------------------------
    Stefan, 24 November 2026, Polyether Polyol - China - EXW: Current Market said USD 1,597 priced
    23 November, the Forecast card said "Last published - November 2026, USD 1,566", and the
    Forecast chart drew 23 November at 1,405. *"A customer would expect this to be the same number
    and any calculations coming from this (like reduce percentage) to stem from there."*

    Three figures from three rules: the mean of that day's prints with China's VAT still in them;
    the median of November so far, net; and the consolidated value, net, one publisher behind
    because the second print landed after the day's consolidation ran. Each was defensible where
    it stood and no reader could have known which he was looking at.

    The rule is the card's own and it has not changed: of the lines drawn on these terms in this
    packaging, the one priced most recently, and where two are equally recent the longer record.
    What changed is that the card no longer owns it. The Forecast page asks here too, so its card,
    its percentage, the point its chart starts from and the report under it are this figure and
    cannot be another one.
    """
    chosen = [line for line in lines_packaged(lines_on(lines, basis), packaging)
              if current_price(line, as_of=as_of)]
    line = max(chosen, key=lambda one: (one.last_day, len(one.points)), default=None)
    return current_price(line, as_of=as_of) if line is not None else None


def drawable_selections(identities: Iterable[dict[str, Any]],
                        observations: Iterable[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Every (chemical, geography, terms, packaging) that puts a line on the chart.

    Stefan, 27 September 2026, told what the selectors add up to: 73 selections draw a chart and
    672 draw nothing. "Obviously the 672 selections a user should not be able to make."

    Below the chemical, each list used to fall back to a standing roster wherever the store knew
    nothing - fifteen geographies and eight Incoterms - so a chemical with no readings offered 120
    complete paths to an empty chart, and five polyol families were each in that position. The
    roster was there to stop an empty list crashing the selector; it stood in for the whole cascade
    instead.

    One pass over the readings, so the page asks once rather than per selection. A reading that has
    been set aside, or that has no value in the currency being shown, does not make a selection
    reachable - the chart would have nothing to draw either way.
    """
    wanted = {
        _text(row.get("identity_key")): row
        for row in identities if _text(row.get("identity_key"))
    }
    out: set[tuple[str, str, str, str]] = set()
    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        source_id = _text(row.get("source_id"))
        if not day or not is_drawn(identity, source_id, day):
            continue
        out.add(market_key(identity, source_id))
    return out


def history_span(identities: Iterable[dict[str, Any]],
                 observations: Iterable[dict[str, Any]],
                 *, chemical: str, geography: str = "") -> tuple[str, str] | None:
    """First and last day we hold a drawn reading for this market, or nothing where we hold none.

    A page that cannot price something today is asked next whether there is anything behind it at
    all. It used to answer "Historic Pricing has what we hold", which is true of a chemical with
    twelve years of record and equally true of one with nothing - the reader goes and looks, and
    half of them find an empty page. Stefan, 1 October 2026: check, and say which it is.
    """
    wanted = {
        _text(row.get("identity_key")): row
        for row in identities
        if _text(row.get("product_family")) == _text(chemical)
        and (not geography or _text(row.get("market_geography")) in set(geographies_for(geography)))
        and _text(row.get("identity_key"))
    }
    if not wanted:
        return None
    days: list[str] = []
    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        if day and is_drawn(identity, _text(row.get("source_id")), day):
            days.append(day)
    return (min(days), max(days)) if days else None


def geographies_with_a_market(drawable, chemical: str) -> tuple[str, ...]:
    return tuple(sorted({geo for chem, geo, _b, _p in drawable if chem == _text(chemical)}))


def terms_with_a_market(drawable, chemical: str, geography: str) -> tuple[str, ...]:
    return tuple(sorted({basis for chem, geo, basis, _p in drawable
                         if chem == _text(chemical) and geo == _text(geography)}))


# How old the newest price may be before a market stops being a current market.
#
# WHERE THIS NUMBER HAS BEEN
#
# A week, from Stefan on 27 September 2026 - "every graph that we have a collect for in the last
# week is part of the Current Market page, and all others go to Historic Pricing" - and then,
# minutes later, "sorry I think too strict, collection monthly". Some of what we collect is
# monthly by nature, and a week moved the regional assessments to Historic Pricing while we were
# still collecting them. Thirty-five days, then two months on his instruction of 28 September, so
# a monthly publisher could miss one print without its market leaving the page.
#
# Stefan, 3 November 2026, looking at Aliphatic Polyester Polyol China on Current Market and seeing
# a 2014-2017 record on it: *"if it split by market then it is wrong, isnt it?? It should split by
# date"*, and then, given the measurement below: *"this whole 2 month is too young, ok one year is
# too old, so we go in the middle 6 months"*.
#
# This replaces `collected_recently` as the Current Market rule, and the change has two halves.
#
# *The age moves from two months to six.* Measured on the live store that day, by the age of the
# newest drawn price: 62 days offered 52 selections across 14 chemicals, one year offered 163
# across 19, and the oldest price on that one-year page was 338 days old - which is the complaint
# Stefan made on 27 September 2026 ("a page headed Current Market that is two thirds history is
# not a current market page") coming back by another door. Six months is his answer to both.
#
# *The question "did WE collect it" is dropped.* It was hiding nine markets that somebody priced
# inside two months, on a page about what the market costs today. Whose reading it is remains a
# fact the chart draws - blue is ours - but it no longer decides which page a market lives on.
#
# What a Yes in Stefan's table may overrule is unchanged and is now the whole of what is left: he
# cannot overrule an old price, and after this there is no other test on Current for him to
# overrule. His No still takes a market off the page, which is the control he actually uses - 34
# of them against 3 Yes on 3 November 2026.
CURRENT_WITHIN_DAYS = 183

# How far back the Current Market chart draws.
#
# The other half of the same ruling, and the half Stefan agreed to first: *"everything older then 1
# year is history"*. A market stays current on six months; the chart under it shows a year, and
# anything older belongs on Historic Pricing, which draws the whole record.
#
# Splitting the pages by market alone left this undone. Current Market drew every market's entire
# record, so Aliphatic Polyester Polyol China - nine readings of our own, five weeks old - opened
# on a chart whose width was 73% an empty hole and whose visible line was a deal record that ended
# in May 2017. Measured the same day across the 14 charts the page drew: PO China 85% hole,
# Aliphatic Polyester 73%, Polyether Polyol China 33%.
DRAWN_ON_CURRENT_DAYS = 365

# How many readings a selection needs before a page will offer it.
#
# Two, because that is what a line is. One reading is a dot the chart does not draw - no markers,
# by Stefan's own ruling of 26 September 2026 - under an axis the plotting library invents a span
# for: Polyether Polyol Canada CIF, one reading dated 1 August 2026, drawn on a grid running 2023
# to 2032 with nothing on it (20 November 2026).
#
# It is deliberately the minimum rather than a judgement about how much history is enough. Enough
# for a CORRELATION is `correlation.MIN_SERIES_WEEKS`, enough for a FORECAST is the engine's to
# decide, and neither is this question. This one is only: will the reader see anything.
#
# Counted in the window the page itself draws, so the two pages answer it about different sets.
# EO United States FOB holds 19 readings from 2003 to August 2026 and one of them inside the last
# year: a dot on Current Market and a real line on Historic Pricing, which is exactly where it
# should end up.
MIN_READINGS_FOR_A_LINE = 2

# How old the newest reading may be before a market has no forecast left to make.
#
# The longest horizon the application offers is twelve weeks, and a horizon is counted from the
# newest reading. So once that reading is more than 84 days old every horizon lands in a month that
# has already happened, and the page is not forecasting anything - on 16 October 2026 Adipic Acid
# Saudi Arabia, last priced in April, headed a card "MAY 2026".
#
# Ninety-one days is thirteen weeks: the twelve-week horizon with a week of margin. The threshold is
# not delicate - at the October 2026 extract, 84 and 91 days excluded exactly the same 32 markets,
# because what survives sits at 16, 46 or 77 days and what fails sits beyond 100. Nothing is near
# the line, and all 32 are UN Comtrade feeds that have stopped rather than feeds running late.
FORECAST_WITHIN_DAYS = 91


def drawn_as_a_line(identities: Iterable[dict[str, Any]],
                    observations: Iterable[dict[str, Any]],
                    *, as_of: date | None = None,
                    within_days: int | None = None) -> set[tuple[str, str, str, str]]:
    """The selections that put a LINE on the chart, not merely a reading in the store.

    `drawable_selections` asks whether a selection has a drawn reading. This asks whether it has
    `MIN_READINGS_FOR_A_LINE` of them, which is the question the chart actually answers, and it
    asks inside the window the page draws: `within_days=None` is the whole record, which is what
    Historic Pricing draws, and `DRAWN_ON_CURRENT_DAYS` is what Current Market draws.

    Stefan, 20 November 2026, sent a screenshot of Polyether Polyol - Canada - CIF: a card reading
    USD 3,270 over an empty ten-year grid. *"So explain this then??"* One reading. The card had
    just been fixed to print it and the chart had nothing to draw, which is the same complaint he
    made about the card one message earlier, one element to the right.

    Readings are counted per selection rather than per identity, by `market_key`, so a market glued
    from two publishers' series is one line here exactly as it is one line on the chart.
    """
    cutoff = ((as_of or date.today()) - timedelta(days=within_days)).isoformat()         if within_days is not None else ""
    wanted = {
        _text(row.get("identity_key")): row
        for row in identities if _text(row.get("identity_key"))
    }
    counted: dict[tuple[str, str, str, str], int] = {}
    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        source_id = _text(row.get("source_id"))
        if not day or not is_drawn(identity, source_id, day):
            continue
        if cutoff and _covers_to(day) < cutoff:
            continue
        key = market_key(identity, source_id)
        counted[key] = counted.get(key, 0) + 1
    return {key for key, held in counted.items() if held >= MIN_READINGS_FOR_A_LINE}


def current_price(line: MarketLine, *, as_of: date | None = None,
                  within_days: int = CURRENT_WITHIN_DAYS) -> tuple[str, float] | None:
    """The reading the Current Market card prints, or nothing where this line has none.

    **The same rule that decides the page**, which is the whole point of it. Stefan, 20 November
    2026, on Polyether Polyol - China - FOB: *"When there is nothing to show, then the drop down
    should not make it look as if there is something to show."*

    The dropdown offers a selection that somebody priced inside `CURRENT_WITHIN_DAYS`. The card
    used to print only readings from a source we fetch, at any age, and the two sets are not the
    same: on the live store that day 18 of the 111 selections the page offered ended on "No current
    price available". Polyether polyol China FOB was one - 104 readings of our own running to
    January 2025, which the chart's one-year window drops, and two from an import dated August and
    October 2026, which the card refused for being somebody else's.

    Narrowing the dropdown to the old card rule was the wrong way round. That rule is the one he
    deleted on 3 November 2026, when a licensed assessment four weeks old was sending nine live
    markets to Historic Pricing: *whose* reading it is stopped deciding the page then, and
    `test_a_market_somebody_else_priced_last_month_is_current_too` is there to keep it that way.
    It was still deciding the card, and that is the gap he is pointing at.

    So the card asks the page's question. Provenance is still on the chart - the stroke's colour
    and the hover say whose reading each one is - and it no longer decides whether there is a price
    at all. The age still does: `_covers_to`, so a monthly price stamped on 1 November is November's
    price for the whole month, the way every other age in this module is asked.

    `points` are ordered oldest first and every one is a measured reading - a bridge lies between
    points, never on one - so the newest is the last, and if that one is outside the window none of
    the others can be inside it.
    """
    if not line.points:
        return None
    day, value = line.points[-1]
    cutoff = ((as_of or date.today()) - timedelta(days=within_days)).isoformat()
    return (day, value) if day and _covers_to(day) >= cutoff else None


def priced_since(identities: Iterable[dict[str, Any]],
                 observations: Iterable[dict[str, Any]],
                 *, from_year: int, as_of: date | None = None,
                 within_days: int | None = FORECAST_WITHIN_DAYS) -> set[tuple[str, str, str, str]]:
    """The selections whose newest drawn reading falls in `from_year` or later.

    What the Forecast page can offer. Its own test for whether a market carries a forecast is
    whether the market is still being priced in the forecast years - carrying three months forward
    from a price that stopped in 2017 is arithmetic, not a forecast - and a page that offers a
    market it will then refuse to forecast is the dead end Stefan named on 28 September 2026:

      "if there is no Forecast that can be made from the time series AND it is only history, then
       no reason to show this timeseries on the page."

    Not the Current Market rule, which is `priced_recently` inside `CURRENT_WITHIN_DAYS`: six
    markets carry a forecast without meeting it - the European monthly assessments running to
    1 July among them. A market can be priced for the forecast without being current.

    `within_days` is the second half of the same question, and it was missing. The year test keeps
    out a history that stopped in 2017; it cannot tell April from October inside the same year, so a
    market whose feed went quiet in January stayed eligible all year and headed a card with a month
    that had already happened. The age is measured on what the reading REFERS to, never on when it
    reached us, or a source that back-fills an old month would look fresh (Stefan, 16 October 2026).

    Pass `within_days=None` to ask the year question alone, which is what the Data & Sources table
    does when it needs to say why a market fell out.
    """
    wanted = {
        _text(row.get("identity_key")): row
        for row in identities if _text(row.get("identity_key"))
    }
    newest: dict[tuple[str, str, str, str], str] = {}
    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        source_id = _text(row.get("source_id"))
        if not day or not is_drawn(identity, source_id, day):
            continue
        key = market_key(identity, source_id)
        stamp = str(day)[:10]
        if stamp > newest.get(key, ""):
            newest[key] = stamp
    today = as_of or date.today()
    cutoff = (today - timedelta(days=within_days)).isoformat() if within_days else ""
    return {key for key, stamp in newest.items()
            if stamp[:4].isdigit() and int(stamp[:4]) >= from_year
            and (not cutoff or _covers_to(stamp) >= cutoff)}


def _covers_to(stamp: str) -> str:
    """The last day the reading speaks for.

    A monthly price is stored against the first of its month, because that is the period it belongs
    to - but it is July's price, not the price on 1 July, and it does not become stale until July
    has. Comparing the stored day directly made a July reading 107 days old in mid-October where
    counting from the month it covers makes it 77, and the Forecast bound sits between those two.
    That is the difference between keeping polyol Europe CIF and dropping it.

    A daily reading is its own day and the month-end would flatter it, so only a stamp that actually
    sits on the first is read this way.
    """
    try:
        day = date.fromisoformat(stamp[:10])
    except ValueError:
        return stamp
    if day.day != 1:
        return stamp
    following = date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)
    return (following - timedelta(days=1)).isoformat()

# `collected_recently` stood here until 3 November 2026. It answered "did WE take a reading on
# this market inside two months", and it was the Current Market rule until Stefan replaced it with
# `priced_recently` at `CURRENT_WITHIN_DAYS`. It is deleted rather than left unused: a function
# that reads like the page rule and is not the page rule is how this application keeps ending up
# with one rule in several copies and the wrong copy running.
#
# The half of it worth keeping is in `provenance_of`, which still decides whether a stroke is
# drawn blue. Who took a reading remains a fact the chart tells; it no longer decides a page.

def drawn_on_current(observations: Iterable[dict[str, Any]], *, as_of: date | None = None,
                     within_days: int = DRAWN_ON_CURRENT_DAYS) -> list[dict[str, Any]]:
    """The readings the Current Market chart draws: the last year of them, and nothing older.

    Stefan, 3 November 2026: *"everything older then 1 year is history"*. Historic Pricing holds
    the whole record and is where the rest of it is read; this is the page about what the market
    costs now, and a chart of twelve years on it is a chart of the wrong thing.

    Aged by `_covers_to`, the same way every other age in this module is asked. A monthly price
    stamped on 1 November is November's price and is not eleven months old on the last day of the
    month, which is the test a straight comparison on `observation_date` would fail.

    A list, not a generator: the caller hands it to `market_lines`, which walks the readings more
    than once.
    """
    cutoff = ((as_of or date.today()) - timedelta(days=within_days)).isoformat()
    kept = []
    for row in observations or []:
        day = _day(row.get("observation_date"))
        if day and _covers_to(day) >= cutoff:
            kept.append(row)
    return kept


def priced_recently(identities: Iterable[dict[str, Any]],
                    observations: Iterable[dict[str, Any]],
                    *, as_of: date | None = None,
                    within_days: int = CURRENT_WITHIN_DAYS) -> set[tuple[str, str, str, str]]:
    """The selections somebody priced inside `within_days`. The Current Market rule.

    It was the limit on a Yes and nothing else until 3 November 2026, when it became the page's
    own rule and `collected_recently` was deleted. Its default is therefore the page's six months
    rather than the two the old rule used.

    What a Yes in Stefan's table may overrule and what it may not (masterplan G). He can overrule
    "we did not collect this ourselves" - that is a judgement about whose price to trust. He cannot
    overrule "this price is 3,586 days old", which is what the Current Market page was doing for
    aliphatic polyester polyol Europe DAP, last priced on 1 January 2017. The Forecast page has
    refused a Yes on that ground since 16 October 2026; this is the same limit for Current.
    """
    today = as_of or date.today()
    cutoff = (today - timedelta(days=within_days)).isoformat()
    wanted = {
        _text(row.get("identity_key")): row
        for row in identities if _text(row.get("identity_key"))
    }
    out: set[tuple[str, str, str, str]] = set()
    for row in observations:
        identity = wanted.get(_text(row.get("identity_key")))
        if identity is None or row.get("price") is None:
            continue
        day = _day(row.get("observation_date"))
        source_id = _text(row.get("source_id"))
        if not day or _covers_to(day) < cutoff or not is_drawn(identity, source_id, day):
            continue
        out.add(market_key(identity, source_id))
    return out
