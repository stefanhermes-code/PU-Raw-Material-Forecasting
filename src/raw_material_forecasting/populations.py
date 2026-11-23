"""One word per population, and the count that goes with it.

Masterplan B. Four panels on Data & Sources counted four different things and called all four
*series*, measured 27 October 2026 by running the page's own functions against the store:

    "Customer pages - 245 of 290 time series shown"     290 four-field MARKETS
    "Series that have gone quiet - 116 of 165"          165 chemical-and-geography pairs
    "Collected series - 388 series, 19,670 readings"    388 market-and-publisher rows
    "... 19,670 readings"                               the readings inside those rows

Three of the four are the same population at three widths - the 290 markets collapse to exactly the
165 pairs, set-identical, and the 388 rows cover 227 of the 290 - and the titles gave a reader no
way to see that. The captions were accurate where they existed ("One row per market read by one
publisher" is exactly right); the titles are what a reader counts with.

So the words are fixed here, once, and the application has no other. Widest first:

    READINGS   one price, for one day, from one publisher
    FEEDS      one market read by one publisher - what the collection actually produces
    SOURCES    one publisher's page, endpoint or import, as the source registry holds it
    LINES      one price kind inside a market (masterplan A item 6: assessed, contract, deal, ask)
    MARKETS    chemical, geography, terms, packaging - `series_curation.market_key`
    PAIRS      a chemical in a geography: a market seen at two of its four fields

A feed and a source are not the same count and never were: 66 sources produced the 388 feeds, and
one source reads several markets. The page kept both numbers and named neither.

**"Series" is not one of them, and no count may use it.** It is the word that slid, and it slid
because every one of these populations is a run of readings over time - they differ only in width,
so the word fits all five and distinguishes none. The store's own key is still called a series key
(`app.domain.series_key`), which is the one place it means something precise: a market plus its
line, as the engine stores it. A *title* that says "series" is telling a reader to count something
the title has not named.

**PAIRS is a phrase, not a noun.** The application has never had a word for a chemical in a
geography - it says "chemical and geography" in prose in nine places - and inventing one here would
add a fifth name for something that is a market seen narrower. So it is said in its own fields.

The counting functions below are the other half: a panel asks this module for its count, so the
number and the word it is printed with cannot come apart. `widths_agree` is the assertion the
masterplan asks the page to carry itself - the quiet panel's population must be the catalogue's
markets collapsed to two fields, which it is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class Population:
    """What a count is counting, and the fields it is counted on."""

    one: str
    many: str
    fields: str

    def count(self, how_many: int) -> str:
        """`1 market`, `290 markets` - the number with its own word on it."""
        return f"{how_many:,} {self.one if abs(how_many) == 1 else self.many}"

    def of(self, part: int, whole: int) -> str:
        """`245 of 290 markets` - a count that names its population."""
        return f"{part:,} of {whole:,} {self.many if abs(whole) != 1 else self.one}"


MARKETS = Population("market", "markets", "chemical, geography, terms and packaging")
FEEDS = Population("feed", "feeds", "one market read by one publisher")
SOURCES = Population("source", "sources", "one publisher's page, endpoint or import")
READINGS = Population("reading", "readings", "one price, one day, one publisher")
LINES = Population("line", "lines", "one price kind inside a market")
PAIRS = Population("chemical in a geography", "chemicals in a geography", "chemical and geography")

ALL = (MARKETS, FEEDS, SOURCES, READINGS, LINES, PAIRS)
# The word no count may carry, and what to say instead. Checked by
# `tests/test_one_word_per_population.py` over every title and caption the pages print.
FORBIDDEN = "series"

# The four fields of a market, in the order `series_curation.market_key` returns them, and the two
# a pair keeps. Named here so a panel collapsing a market does not pick its own two.
MARKET_FIELDS = ("chemical", "geography", "terms", "packaging")
PAIR_FIELDS = MARKET_FIELDS[:2]


def _at(row: Any, name: str) -> str:
    value = row.get(name) if isinstance(row, dict) else getattr(row, name, "")
    return str(value or "").strip()


def markets_in(rows: Iterable[Any]) -> set[tuple[str, ...]]:
    """The distinct markets these rows stand for, at all four fields."""
    return {tuple(_at(row, name) for name in MARKET_FIELDS) for row in rows or ()}


def pairs_in(rows: Iterable[Any]) -> set[tuple[str, str]]:
    """The same rows collapsed to a chemical in a geography.

    Takes catalogue rows or anything else carrying `chemical` and `geography` - the quiet watch's
    own rows do, which is what makes the two panels checkable against each other.
    """
    return {(_at(row, "chemical"), _at(row, "geography")) for row in rows or ()}


def widths_agree(catalogue_rows: Sequence[Any], watch_rows: Sequence[Any]) -> str:
    """Empty when the two panels count one population at two widths; else what differs.

    Masterplan B asks for exactly this, in one place: *"the quiet-series count must equal the
    catalogue's markets collapsed to two fields, which today it does."* Measured 28 October 2026:
    165 and 165, set-identical. The page prints the answer rather than the plan asserting it once.
    """
    catalogue = pairs_in(catalogue_rows)
    watched = pairs_in(watch_rows)
    if catalogue == watched:
        return ""
    missing = sorted(catalogue - watched)
    extra = sorted(watched - catalogue)
    said = []
    if missing:
        said.append(f"{len(missing)} in the catalogue and not watched "
                    f"({', '.join(f'{a} {b}' for a, b in missing[:3])})")
    if extra:
        said.append(f"{len(extra)} watched and not in the catalogue "
                    f"({', '.join(f'{a} {b}' for a, b in extra[:3])})")
    return ("The two panels no longer count one population at two widths: " + "; ".join(said)
            + ". One of them is reading the store differently from the other.")
