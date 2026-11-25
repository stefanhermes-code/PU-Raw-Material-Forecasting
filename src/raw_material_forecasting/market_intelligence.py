"""The Market Intelligence page's content, computed rather than written.

The page answers one question - what is going on in the TDI market in China - in three parts:

  Current Market Situation   who can make it, where, and how much
  Market Developments        what has happened, dated, with the kind of thing it is
  Impact                     what it means at three horizons

Everything on the page is derived here so the numbers cannot drift from each other. The Wanhua share
in the Impact paragraph is the same arithmetic as the bar in the owner table, because it is the same
function; the earlier prototype carried both as prose and they would have parted company the first
time a capacity changed.

Two data files hold what the engine cannot collect. Plant capacity is not published daily by anybody
and does not change weekly, so the roster is a reviewed file with each revision carrying the reason it
was revised. The dated developments file holds the same: the turnarounds and capacity changes read out
of industry reporting, which no live source hands us as structured rows.

Live signals from the engine's own collection are merged in on top, so the section grows on its own as
the sourcing lane collects. A signal that we cannot say anything useful about is NOT shown: when the
best the engine can offer is "no direct link to the portfolio", it is saying it does not know why the
reader should care, and a row like that costs a line of the screen to say nothing.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from . import intelligence_register as register
from .chemicals import canonical_code, display_chemical

# How far up a chain a signal may sit and still be shown. The same reach the relationship study uses
# for its full-chain measurement, so the two pages agree about what is upstream of what.
MAX_CHAIN_STEPS = 8

DATA_DIR = Path("data/market_intelligence")

# One market per set of files, named for the market: tdi_china_capacity.csv, pmdi_china_*.csv.
# The page was written for TDI China and read three fixed filenames, so selecting another chemical
# on the application showed TDI's plants under its own heading. The names were already in this
# shape; what was missing was letting the page ask for a different one.
DEFAULT_MARKET = ("TDI", "China")


def market_slug(chemical: str, geography: str) -> str:
    """"TDI", "China" -> "tdi_china". The stem every file for a market shares."""
    def clean(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return f"{clean(chemical)}_{clean(geography)}"


def capacity_file(chemical: str = DEFAULT_MARKET[0], geography: str = DEFAULT_MARKET[1]) -> str:
    return f"{market_slug(chemical, geography)}_capacity.csv"


def pipeline_file(chemical: str = DEFAULT_MARKET[0], geography: str = DEFAULT_MARKET[1]) -> str:
    return f"{market_slug(chemical, geography)}_pipeline.csv"


def developments_file(chemical: str = DEFAULT_MARKET[0], geography: str = DEFAULT_MARKET[1]) -> str:
    return f"{market_slug(chemical, geography)}_developments.csv"


def has_register(chemical: str, geography: str, directory: Path = DATA_DIR) -> bool:
    """Whether a capacity roster exists for this market at all."""
    return (Path(directory) / capacity_file(chemical, geography)).exists()


def has_intelligence(chemical: str, geography: str, directory: Path = DATA_DIR) -> bool:
    """Whether this market has anything to show: a capacity roster, or its own developments.

    Polyether polyol in China has developments and no roster, which is a market worth opening and is
    exactly the state a roster-only check would have called empty.
    """
    return (has_register(chemical, geography, directory)
            or (Path(directory) / developments_file(chemical, geography)).exists()
            or has_signals(chemical, geography, directory))


# A CHEMICAL WITH SIGNALS IS A CHEMICAL WITH SOMETHING BEHIND IT (v1.2.132, 25 November 2026).
#
# The rule above this one asked for a file: a researched plant roster, or a researched list of
# developments. Three chemicals have those. So the page offered three, while the register and the
# producers' own announcements named thirteen - pure MDI alone had 28 signals and no page.
# Stefan, an hour and a half before a customer demo: "'a chemical with nothing behind it should not
# be offered' is right but I do not believe there are no signals for the other chemicals. I dont
# care about the plant... that is a restriction you invented yourself."
#
# It was. What he ruled out on 11 October was a choice with NOTHING behind it. A signal that names
# the chemical is something, whether or not anybody has listed its plants.
#
# "Names the chemical" is meant strictly. An item that names no chemical bears on every market and
# is shown on every page, and counting it here would open a page for every chemical we know of -
# the 165 dead options that the October ruling removed.
_NAMED: dict[str, tuple] = {}


def _signals_that_name_a_chemical(directory: Path):
    """The register's printable rows that name a chemical, and where producers' signals place each
    chemical. Read again only when one of the two files has changed."""
    directory = Path(directory)
    feed = directory.parent / "producer_signals.json"
    stamp = tuple(file.stat().st_mtime_ns if file.exists() else 0
                  for file in (register.path(directory), feed))
    key = str(directory.resolve())
    held = _NAMED.get(key)
    if held is not None and held[0] == stamp:
        return held[1], held[2]
    rows = [row for row in register.load(directory) if row.chemical_list and row.is_printable]
    places: dict[str, set[str]] = {}
    if feed.exists():
        from .producer_signals import CUSTOMER_VISIBLE_CATEGORIES, load_producer_signals

        for signal in load_producer_signals(feed):
            if (signal.verification_status != "VERIFIED"
                    or signal.category not in CUSTOMER_VISIBLE_CATEGORIES or not signal.chemical):
                continue
            places.setdefault(canonical_code(signal.chemical), set()).add(
                str(signal.geography or "").strip())
    _NAMED[key] = (stamp, rows, places)
    return rows, places


def has_signals(chemical: str, geography: str, directory: Path = DATA_DIR) -> bool:
    """Whether a signal that names this chemical bears on this market.

    A producer's own announcement counts where it says it happened. An item from one of HTC's
    reports counts wherever the page would show it for this chemical: the place it names, every
    Asian market for one that says Asia, every market for one that says worldwide.
    """
    rows, places = _signals_that_name_a_chemical(directory)
    code = canonical_code(chemical)
    if geography in places.get(code, ()):
        return True
    return any(code in {canonical_code(named) for named in row.chemical_list}
               and register.covers(row, chemical=chemical, geography=geography)
               for row in rows)


def markets_with_intelligence(chemical: str, geographies: Iterable[str],
                              directory: Path = DATA_DIR) -> list[str]:
    """The geographies worth offering for this chemical, in the order they were given.

    The selector offered a fixed roster of five geographies against eleven chemicals - fifty-five
    combinations, of which three have anything behind them. Every other one drew a heading over a
    sentence saying nothing was loaded. Current Market cascades its list off what the store holds;
    this is the same idea against the files this page reads.
    """
    return [geography for geography in geographies
            if has_intelligence(chemical, geography, directory)]


# Signals are not narrowed to the selected market, and a reader looking at European producer letters
# under a China selection has to be told that rather than left to read it as a filter that failed. A
# European producer raising by 250 while Chinese material trades near 1,400 EUR/MT is what pulls
# Chinese volume into Europe; it is news for a Chinese buyer precisely because it happened elsewhere.
SIGNALS_ARE_WORLDWIDE = (
    '<div class="mi-note">Signals are collected worldwide and are not narrowed to the geography '
    'selected above. What a producer does in one region sets what buyers in every other one are '
    'quoted next.</div>'
)

# Kept so anything still naming them resolves to the market the page was built for.
CAPACITY_FILE = capacity_file()
PIPELINE_FILE = pipeline_file()
DEVELOPMENTS_FILE = developments_file()

# What each chemical is made from, for the Feedstock line of the legend. A reader is told what a tag
# means in terms of the material in front of them, not in terms of TDI whatever they selected.
FEEDSTOCKS: dict[str, str] = {
    "TDI": "toluene, chlorine, carbon monoxide, nitric acid",
    "PMDI": "aniline, formaldehyde, chlorine, carbon monoxide",
    "MDI": "aniline, formaldehyde, chlorine, carbon monoxide",
    # Keyed on both the store's code and the name on the screen, because the page passes whichever
    # the selector gave it.
    "POLYOL": "propylene oxide, ethylene oxide, and the initiator",
    "POLYETHER POLYOL": "propylene oxide, ethylene oxide, and the initiator",
    "POLYETHER_POLYOL": "propylene oxide, ethylene oxide, and the initiator",
    "HR POLYOL": "propylene oxide, an ethylene oxide cap, and the initiator",
    "HR_POLYOL": "propylene oxide, an ethylene oxide cap, and the initiator",
    "GRAFT POLYOL (POP)": "a polyether base polyol, styrene and acrylonitrile",
    "GRAFT POLYOL": "a polyether base polyol, styrene and acrylonitrile",
    "GRAFT_POLYOL": "a polyether base polyol, styrene and acrylonitrile",
    "PHD POLYOL": "a polyether base polyol, TDI and an amine",
    "PHD_POLYOL": "a polyether base polyol, TDI and an amine",
    "PIPA POLYOL": "a polyether base polyol, TDI and an alkanolamine",
    "VISCO POLYOL": "propylene oxide and a high ethylene oxide content",
    "VISCO_POLYOL": "propylene oxide and a high ethylene oxide content",
    "FATTY ACID ESTER POLYOL": "dimer and fatty acids, largely renewable",
    "FATTY_ACID_ESTER_POLYOL": "dimer and fatty acids, largely renewable",
    "PIPA_POLYOL": "a polyether base polyol, TDI and an alkanolamine",
    "ALIPHATIC POLYESTER POLYOL": "adipic acid and a glycol",
    "ALIPHATIC_POLYESTER_POLYOL": "adipic acid and a glycol",
    "AROMATIC POLYESTER POLYOL": "phthalic anhydride or recycled PET, and a glycol",
    "AROMATIC_POLYESTER_POLYOL": "phthalic anhydride or recycled PET, and a glycol",
}

# The Type vocabulary, and what each label means. Shown as the legend, so a reader never has to guess
# what a tag is claiming.
TYPE_LEGEND: tuple[tuple[str, str], ...] = (
    ("Capacity", "Nameplate capacity changes: a line added, closed, debottlenecked or written off."),
    ("Operations", "Running status: turnaround, outage, force majeure, reduced rate."),
    ("New venture", "A plant that does not exist yet: announced, permitted, financed, under construction."),
    ("Feedstock", "A material TDI is made from: toluene, chlorine, carbon monoxide, nitric acid."),
    ("Company", "Ownership, investment, financial position, insolvency."),
    ("Trade", "Export volumes, anti-dumping cases, duties, licensing."),
    ("Pricing", "A producer's own price action: listed price, settlement price, increase letter."),
    ("Regulation", "Environmental, safety or energy action affecting a plant or province."),
    # Added 5 November 2026. The three classes below were being collected and shown and had no
    # name, so nothing on the page could reason about them - see `register_type`.
    ("Logistics", "Getting it here: shipping lanes, transits, freight, war-risk cover, ports."),
    ("Market", "Where the market itself is: price direction, demand, buying behaviour, cover held."),
    ("Product", "A grade, additive or formulation brought to market."),
)

# Engine signal category -> the Type a reader sees.
_CATEGORY_TYPE: dict[str, str] = {
    "CAPACITY_ADDITION": "Capacity",
    "CAPACITY_EXPANSION": "Capacity",
    "DEBOTTLENECK": "Capacity",
    "PERMANENT_CLOSURE": "Capacity",
    "STRUCTURAL_REDUCTION": "Capacity",
    "CAPACITY_INVESTMENT": "New venture",
    "PLANT_STARTUP": "Operations",
    "SHUTDOWN": "Operations",
    "PLANNED_MAINTENANCE": "Operations",
    "FORCE_MAJEURE": "Operations",
    "INCIDENT": "Operations",
    "CURTAILMENT": "Operations",
    "CORPORATE_VENTURE": "Company",
    "CORPORATE_EXIT": "Company",
    "PRICE_INCREASE": "Pricing",
    "PRICE_DECREASE": "Pricing",
    "PRICE_ACTION": "Pricing",
}

# A relevance sentence that says the engine found no link. Publishing the row anyway asks the reader
# to work out why it is on their screen, and the honest answer is that nobody knows.
_NO_LINK = "no direct link"

# Types that answer "what is happening in the market". A producer's own price action is a Pricing
# signal and belongs on the page; a market price move is a price, and lives on Current Market Pricing.
MATERIALITY_ORDER: tuple[str, ...] = (
    "Capacity", "New venture", "Company", "Regulation", "Trade", "Logistics", "Operations",
    "Feedstock", "Pricing", "Market", "Product",
)


# ---------------------------------------------------------------------------------------------
# The register speaks a different language from the page, and nobody was translating.
#
# `intelligence_register` stores a `kind` guessed by `briefing_reader.kind_of`, whose vocabulary is
# Price action / Corporate / Product / New venture / Operations / Trade / Regulation - and a blank
# where no word matched. `register_developments` passed that straight through as the page's Type.
#
# Counted against the live register on 5 November 2026, TDI China: 51 of 90 items reached the page
# with NO type at all, and 20 more carried a word the page's vocabulary does not contain. Impact is
# computed by type. So seventy-one of the newest, best-sourced items on the page - HTC's own weekly
# briefings and nine Strait of Hormuz special reports - were invisible to every conclusion drawn
# above them. The page's Impact stood on the researched CSV and the engine feed alone, both of them
# months old, which is exactly why it read the same every week while the table underneath changed.
# ---------------------------------------------------------------------------------------------

# The register's word -> the page's word. Everything the register can store has an answer here.
_REGISTER_TYPE: dict[str, str] = {
    "price action": "Pricing",
    "corporate": "Company",
    "operations": "Operations",
    "regulation": "Regulation",
    "trade": "Trade",
    "new venture": "New venture",
    "product": "Product",
    # Already in the page's own words, where a row was typed by hand.
    "capacity": "Capacity", "feedstock": "Feedstock", "pricing": "Pricing",
    "company": "Company", "logistics": "Logistics", "market": "Market",
}

# Whole words, not substrings. Written as a plain tuple of fragments first, these matched "port "
# inside "report ", "ports" inside "exports" and "reports", "merg" inside "emergency", "import"
# inside "important" and "stake" inside "mistake" - so a sentence about an emergency response was
# filed as a merger and most of the page was reclassified as shipping. Every pattern below is
# anchored on word boundaries for that reason, and the fragments that are meant to be stems say so
# with an explicit suffix group.
def _words(*fragments: str) -> "re.Pattern[str]":
    return re.compile(r"\b(?:" + "|".join(fragments) + r")\b", re.I)


# A ship is not a plant. The register's own guesser reads "incident" in "GFS PRIDE was confirmed
# sunk" and files it under Operations, where it would be counted toward a line about plant
# turnarounds. Logistics is tested first and overrides the stored kind for that reason.
_LOGISTICS = _words(
    "hormuz", "bab[ -]el[ -]mandeb", "bab[ -]al[ -]mandab", "red sea", "suez", "straits?",
    "gulf of oman", "persian gulf", "tankers?", "vessels?", "ships?", "shipping", "shipments?",
    "freight", "transits?", "voyages?", "convoys?", "war[ -]risk", "maritime", "ports?",
    "seaports?", "berths?", "lanes?", "containers?", "carriers?", "bunker", "cape routing",
    "mine[ -]laying", "sea routes?", "seafarers?", "imo", "crews?", "blockades?", "naval",
    "chokepoints?",
)
# Second pass for a blank kind, in the page's own vocabulary.
_TYPE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("Pricing", _words("price increase", "increase letter", "surcharges?", "raises its",
                       "price update")),
    ("Capacity", _words("debottleneck\\w*", "nameplate", "mothball\\w*", "written off",
                        "permanent closure")),
    ("New venture", _words("new plant", "under construction", "feasibility", "groundbreaking",
                           "expansions?")),
    ("Operations", _words("maintenance", "turnarounds?", "shutdowns?", "restarts?", "outages?",
                          "force majeure")),
    ("Feedstock", _words("toluene", "chlorine", "propylene oxide", "aniline", "benzene",
                         "naphtha", "crude")),
    ("Regulation", _words("anti-dumping", "duty", "duties", "sunset review", "emissions?",
                          "inspections?")),
    ("Trade", _words("exports?", "imports?", "customs", "tariffs?")),
    ("Company", _words("acquisitions?", "acquir\\w+", "mergers?", "merging", "stakes?",
                       "joint ventures?", "insolven\\w+", "divest\\w*", "ownership",
                       "shareholdings?", "takeovers?")),
)


def register_type(kind: str, headline: str = "", context: str = "") -> str:
    """The page's Type for one register item. Never blank.

    A row the page cannot type is a row Impact cannot read, and a row Impact cannot read may as
    well not have been collected. "Market" is the honest last answer for an item that describes the
    state of the market rather than a discrete event - it is where most of the blanks belong, and
    it is a real class, not a bucket for failures.
    """
    text = f"{headline} {context}"
    if _LOGISTICS.search(text):
        return "Logistics"
    mapped = _REGISTER_TYPE.get(str(kind or "").strip().lower())
    if mapped:
        return mapped
    for label, pattern in _TYPE_PATTERNS:
        if pattern.search(text):
            return label
    # The last answer, and a real class rather than a bucket for failures. What falls through here
    # is almost all HTC's own reading of where the market stands - the price direction, demand,
    # buying behaviour, the cover buyers are holding - which is exactly what "Market" means in the
    # legend. Counted on 5 November 2026 it was 9 items of 122 on TDI China.
    return "Market"


@dataclass(frozen=True)
class Plant:
    plant: str
    province: str
    owner: str
    owner_group: str
    kt: int
    note: str            # a market fact, and the only thing that reaches the page
    internal_note: str   # how the figure was reconciled. Never rendered - see _plant_note.


@dataclass(frozen=True)
class PipelinePlant:
    owner: str
    site: str
    province: str
    kt: int
    due: date | None
    status: str
    note: str


UNDISCLOSED = "Source not disclosed"


@dataclass(frozen=True)
class Development:
    """One dated event, and who said it happened.

    `attribution` is the answer to "who says so", printed beside the event. It is required. A row
    that cannot name where it came from does not reach the page - see `load_developments`.

    Some of what HTC holds is licensed or member-confidential and may not be reproduced outside the
    company: the EUROPUR market report, the subscription isocyanate letters. Those are legitimate
    sources and they are used. They are attributed as "Source not disclosed", which tells the reader
    the honest thing - there is a source, and it is not ours to name.
    """
    date: date
    precision: str          # DAY / MONTH / YEAR / PERIOD - how precisely the source dated it
    development: str
    type: str
    source: str             # "researched" or the engine source_id that produced it
    attribution: str = ""   # who says so, kept off the page, see `developments_block`
    confidence: str = ""    # the briefing's own Status line: CONFIRMED or INDICATION, else blank
    # Does the signal NAME the chemical whose page it is on? The register scopes an item by the
    # document's own Scope line, and a row naming no chemical lands on every market - correctly,
    # because freight through Hormuz reaches all of them. See `split_developments`.
    specific: bool = False
    context: str = ""       # why it matters, printed under the signal
    implication: str = ""   # what to do about it, where the source wrote one
    subject: str = ""       # the chemical the event is about, where it names one
    out_from: date | None = None   # an outage's own window and size, where the source gives them;
    out_to: date | None = None     # the Supply line of Impact is worked out from these
    out_kt: int | None = None


# Nobody is named in a customer's report. That rule was written down on 8 October 2026, with the
# list of who it means and a `names_a_source` to test text against it, and it was wired into the
# Import review - where it warns a person - and into nothing that renders. So the warning was
# shown, the row was taken anyway, and the page printed "Secondary SunSirs PriceSeek report: ..."
# as a headline. Stefan, 6 November 2026: "Make sure these market signals do not leak sources."
#
# The rule is enforced here, on every row from every feed, rather than copied: the list lives in
# `intelligence_register` beside the import that checks against it, and a second copy is how two
# lists of the same thing end up disagreeing.
def published_words(item: Development) -> Development | None:
    """One row as a customer may read it, or None where it cannot be shown at all.

    All three feeds come through here - the researched file, the engine's collected signals and
    the register - because all three are written by people reading somebody else's page, and the
    wording travels with the fact.

    A row that still names a service after `source_free` has run is dropped rather than printed.
    The shapes that function knows are the ones we have seen, not the ones that exist, and a
    missing row costs less than a named subscription does.
    """
    cleaned = replace(item,
                      development=register.source_free(item.development),
                      context=register.source_free(item.context),
                      implication=register.source_free(item.implication))
    if not cleaned.development:
        # The sourcing WAS the headline and nothing of the event survived it.
        return None
    if register.names_a_source(cleaned.development, cleaned.context, cleaned.implication):
        return None
    return cleaned


@dataclass(frozen=True)
class Run:
    """A stretch of one kind of signal, standing in the open table for the rows it replaced.

    Stefan, 6 November 2026: "Ok collapse." Nine of the eleven wider rows on TDI China were one
    shipping sequence - a supertanker alight, an LPG carrier struck, a drone intercepted, a rising
    IMO count. Each true, each worth its own row in the record, and together a column of
    near-identical maritime prose a reader scrolled past to reach anything else.

    What a Run says is COUNTED AND DATED AND NOTHING ELSE. It does not summarise the events it
    replaces. Composing one sentence out of nine is how a page begins asserting what no document
    said, and the conclusion a reader needs is already written above it: the Logistics line of
    Impact stands on this same evidence and says the chain is graded Disrupted.
    """
    type: str
    count: int
    oldest: date
    newest: date


def _rows(directory: Path, name: str) -> list[dict]:
    path = Path(directory) / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if any((value or "").strip() for value in row.values())]


def _date_or_none(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value and str(value).strip() else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    text = str(value or "").strip()
    return int(float(text)) if text else None


def load_plants(directory: Path = DATA_DIR, *, chemical: str = DEFAULT_MARKET[0],
                geography: str = DEFAULT_MARKET[1]) -> list[Plant]:
    plants = [
        Plant(
            plant=row["plant"].strip(),
            province=row.get("province", "").strip(),
            owner=row.get("owner", "").strip(),
            owner_group=row.get("owner_group", "").strip() or row.get("owner", "").strip(),
            kt=_int(row.get("kt")) or 0,
            note=row.get("note", "").strip(),
            internal_note=row.get("internal_note", "").strip(),
        )
        for row in _rows(directory, capacity_file(chemical, geography))
    ]
    return sorted(plants, key=lambda item: (-item.kt, item.plant))


def load_pipeline(directory: Path = DATA_DIR, *, chemical: str = DEFAULT_MARKET[0],
                  geography: str = DEFAULT_MARKET[1]) -> list[PipelinePlant]:
    out = []
    for row in _rows(directory, pipeline_file(chemical, geography)):
        due = row.get("due", "").strip()
        out.append(PipelinePlant(
            owner=row.get("owner", "").strip(),
            site=row.get("site", "").strip(),
            province=row.get("province", "").strip(),
            kt=_int(row.get("kt")) or 0,
            due=date.fromisoformat(due) if due else None,
            status=row.get("status", "").strip(),
            note=row.get("note", "").strip(),
        ))
    return sorted(out, key=lambda item: (item.due or date.max))


def owner_shares(plants: list[Plant]) -> list[tuple[str, int, float, int]]:
    """(owner group, kt, share of total, number of sites), largest first.

    A roster line with no owner group is an aggregate of several producers rather than one of them.
    EUROPUR reports twelve smaller Chinese polyol plants on a single line of 1,980 kt; counted as an
    owner it becomes the largest holder of Chinese capacity at 20%, and the concentration reading
    then says one group holds the most when twelve do. It still counts toward the total, because the
    capacity is real - it is only the ranking it has no place in.
    """
    total = sum(plant.kt for plant in plants)
    grouped: dict[str, list[Plant]] = {}
    for plant in plants:
        if not str(plant.owner_group or "").strip():
            continue
        grouped.setdefault(plant.owner_group, []).append(plant)
    rows = [
        (owner, sum(item.kt for item in group), (sum(item.kt for item in group) / total * 100) if total else 0.0,
         len(group))
        for owner, group in grouped.items()
    ]
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def headline_figures(plants: list[Plant], pipeline: list[PipelinePlant],
                     geography: str = DEFAULT_MARKET[1]) -> dict[str, str]:
    total = sum(plant.kt for plant in plants)
    shares = owner_shares(plants)
    arriving = sum(item.kt for item in pipeline)
    figures = {
        f"{geography} capacity": f"{total:,} kt",
        "Plants": str(len(plants)),
        "Owners": str(len(shares)),
        "Largest owner": f"{shares[0][2]:.0f}%" if shares else "-",
    }
    if pipeline:
        year = min((item.due.year for item in pipeline if item.due), default=None)
        figures[f"Arriving {year}" if year else "Arriving"] = f"+{arriving:,} kt"
    return figures


# A capacity figure in the headline is what separates a line starting for the FIRST time from a
# plant coming back off a turnaround. The first is capacity; the second is running status.
_CAPACITY_FIGURE_RE = re.compile(r"\d[\d,.]*\s*(?:t/y|kt|tonnes?/year|万吨)", re.I)


def type_of(category: str, relevance: str = "", headline: str = "") -> str:
    """The Type a reader sees, which is about what the event IS, not which rule matched it.

    Two corrections on the raw category, both to match what the legend promises:

    A price move on something TDI is MADE FROM is a feedstock signal. The legend defines Pricing as a
    producer's own price action on the product itself; chlorine going up is a cost story, and filing
    it under Pricing buries it among announcements about a different market.

    A plant "starting up" with a capacity figure attached is a new line, not a restart. The legend
    puts running status under Operations and a line arriving under Capacity.
    """
    category = str(category or "").strip().upper()
    relevance = str(relevance or "").strip()
    if relevance.startswith(("Feedstock", "Intermediate")):
        return "Feedstock"
    if category == "PLANT_STARTUP" and _CAPACITY_FIGURE_RE.search(str(headline or "")):
        return "Capacity"
    return _CATEGORY_TYPE.get(category, "Company")


PUBLISHERS: dict[str, str] = {
    "covestro_press": "Covestro",
    "echemi_news": "ECHEMI",
    "longzhong_tdi": "Longzhong",
    "sci99_pmdi": "SCI99",
    "sunsirs_news": "SunSirs",
}


def publisher_of(source_id: str) -> str:
    """Who published a collected signal. An id we have no name for is still a source."""
    key = str(source_id or "").strip().lower()
    return PUBLISHERS.get(key) or (key or UNDISCLOSED)


# What a publisher calls a chemical is not always what the route graph calls it. Mysteel prints
# "liquid chlorine" for the chlorine that reaches TDI through phosgene, and a signal about it was
# being dropped from TDI's own page for want of the word.
_GRAPH_ALIASES = {
    "LIQUID_CHLORINE": "CHLORINE",
    "LIQUID_AMMONIA": "AMMONIA",
    "PROPYLENE_OXIDE": "PO",
    "ETHYLENE_OXIDE": "EO",
    "POLYMERIC_MDI": "PMDI",
}


def _graph_code(graph, chemical: str) -> str:
    """The route graph's own code for a chemical, allowing for how publishers spell it."""
    code = canonical_code(chemical)
    if graph is not None and graph.node(code) is not None:
        return code
    mapped = _GRAPH_ALIASES.get(code)
    if mapped and graph is not None and graph.node(mapped) is not None:
        return mapped
    return mapped or code


def chain_relevance(graph, target: str, chemical: str) -> str | None:
    """Why a signal about `chemical` matters to somebody buying `target`, on this page.

    The stored `market_relevance` sentence is written once, by whoever recorded the signal, against
    whatever market they had in mind - and every one of them was written against TDI. So a polyether
    polyol page explained liquid chlorine as "Feedstock for TDI, via phosgene" (chlorine does reach
    polyether polyol, by an entirely different route) and carried pMDI as "Competes with TDI"
    (pMDI is not in the polyether chain at all).

    The route graph already knows the answer for whichever chemical the reader selected, so it is
    asked instead of a stored sentence. Returns None when the graph has no connection, which is the
    signal to leave the row off this page - it will still appear on the page of a chemical it does
    reach.
    """
    if graph is None or not chemical:
        return None
    code = _graph_code(graph, chemical)
    goal = _graph_code(graph, target)
    if code == goal:
        return None
    candidate = next((c for c in graph.upstream_candidates(goal, max_priced_steps=MAX_CHAIN_STEPS)
                      if c.chemical == code), None)
    if candidate is None:
        return None
    name = display_chemical(canonical_code(target))
    if candidate.priced_steps <= 1:
        return f"Direct feedstock for {name}."
    # `path` runs from the target back to the chemical and includes the intermediates nobody prices,
    # which is exactly what a reader needs to see: chlorine reaches polyether polyol through
    # propylene chlorohydrin and propylene oxide, and reaches TDI through phosgene.
    # `path` runs from the target backwards to the chemical, so it is reversed to read the way the
    # chemistry runs: chlorine becomes propylene chlorohydrin, which becomes propylene oxide, which
    # makes the polyol.
    between = [graph.node(step).display_name if graph.node(step) else step
               for step in reversed(candidate.path[1:-1])]
    if len(between) > 1:
        via = f" via {', '.join(between[:-1])} and {between[-1]}"
    elif between:
        via = f" via {between[0]}"
    else:
        via = ""
    return f"Feedstock for {name}{via}."


def _names(sentence: str, graph, chemical: str) -> bool:
    """Whether a recorded relevance sentence is about this chemical."""
    node = graph.node(_graph_code(graph, chemical)) if graph is not None else None
    words = {str(chemical).replace("_", " ")}
    if node is not None:
        words |= {node.display_name, node.full_name or node.display_name}
    low = (sentence or "").lower()
    return any(word and word.lower() in low for word in words)


_SUBJECTLESS = re.compile(r"(entered a venture|withdrawing from a business|is investing in a business)\s*$", re.I)


def signal_developments(signals, *, graph=None, chemical: str | None = None) -> list[Development]:
    """Live engine signals as dated developments, dropping the ones with nothing to say.

    A signal whose relevance reads "no direct link to the portfolio" is the engine stating it could
    not connect the event to anything the reader buys. That is worth recording and not worth showing.

    Where the route graph is available, relevance is worked out for the chemical the reader selected
    rather than read from the stored sentence, and a signal the graph cannot connect to that chemical
    is left off. A signal naming no chemical at all stays: a producer restructuring itself is market
    intelligence whether or not the headline said which product it touched.
    """
    out: list[Development] = []
    for signal in signals:
        relevance = (getattr(signal, "market_relevance", "") or "").lower()
        if _NO_LINK in relevance:
            continue
        signal_chemical = getattr(signal, "chemical", "") or ""
        context = (getattr(signal, "market_relevance", "") or "").strip()
        if graph is not None and chemical and signal_chemical:
            computed = chain_relevance(graph, chemical, signal_chemical)
            if computed is not None:
                context = computed
            elif _graph_code(graph, signal_chemical) == _graph_code(graph, chemical):
                # The signal is about this material itself. The headline already says so.
                context = ""
            else:
                # Neither this chemical nor upstream of it on its routes (Stefan, 22 September
                # 2026). "Competes with TDI" and "bought with TDI in flexible foam" came off the TDI
                # page here: they are other markets, and they are on their own pages.
                continue
        if not signal_chemical and _SUBJECTLESS.search(getattr(signal, "what_changed", "") or ""):
            # "Dow Chemical is withdrawing from a business" names no business. A generic signal
            # stays on every page; one that says nothing stays on none.
            continue
        out.append(Development(
            date=signal.announced_at,
            precision="DAY",
            development=signal.what_changed,
            type=type_of(signal.category, signal.market_relevance, signal.what_changed),
            source=getattr(signal, "signal_id", "") or "engine",
            # A collected signal always knows its publisher; it was read off their page.
            attribution=publisher_of(getattr(signal, "source_id", "")),
            # The engine already works out what the event has to do with this market. Printing it
            # turns "Shandong Xinfa lowered its liquid chlorine price" into something a reader who
            # does not know the chain can act on.
            context=context,
            subject=signal_chemical,
            specific=bool(signal_chemical),
            out_from=getattr(signal, "effective_from", None),
            out_to=getattr(signal, "effective_to", None),
        ))
    return out


def register_developments(directory: Path = DATA_DIR, *, chemical: str = DEFAULT_MARKET[0],
                          geography: str = DEFAULT_MARKET[1]) -> list[Development]:
    """What HTC's own weekly documents said, for this market.

    The briefing, the Hormuz special report and the Price Monitor are imported into one register,
    and until 16 October 2026 that register was read by the period report and by nothing else. The
    Market Intelligence page - the page whose whole subject it is - showed the hand-researched files
    and the engine's collected signals, and never looked at it. So Stefan uploaded four briefings
    and nine special reports, the newest on 12 October, and the page went on saying nothing had
    happened since 16 September.

    Attribution is HTC Global. These items are our own writing out of the Observatory and PI3,
    which is the reason they can be printed in a customer's report at all, and `Development`
    requires an answer to "who says so" before anything reaches the page.
    """
    out: list[Development] = []
    for entry in register.for_market(register.load(directory), chemical=chemical,
                                     geography=geography):
        # The day the event happened where the document gave one, else the day it was reported.
        # Both are real answers; a reported date is never passed off as an event date.
        when = entry.day or entry.reported_on
        if when is None:
            continue
        out.append(Development(
            date=when,
            precision=entry.precision if entry.day else "DAY",
            development=entry.headline,
            # Translated, not passed through: see `register_type`. Until 5 November 2026 this was
            # `entry.kind`, and most of what the briefings carried arrived with no type at all.
            type=register_type(entry.kind, entry.headline, entry.context),
            source="briefing",
            attribution=entry.attribution.strip() or "HTC Global",
            # The briefing graded every item CONFIRMED or INDICATION and the page threw the answer
            # away, so a channel rumour was set in the same type as a confirmed sinking. Carried
            # now, and printed - see `developments_block`.
            confidence=entry.confidence.strip().upper(),
            specific=bool(entry.chemical_list),
            context=entry.context,
            implication=entry.implication,
            # Left blank deliberately: `subject` is what the route graph narrows a collected signal
            # by, and these items are already bound to their markets by the register's own reading
            # of the document's Scope line. Narrowing them twice would drop the broad ones.
            subject="",
        ))
    return out


def load_developments(directory: Path = DATA_DIR, signals=None, *,
                      chemical: str = DEFAULT_MARKET[0],
                      geography: str = DEFAULT_MARKET[1], graph=None) -> list[Development]:
    """The researched developments and the live ones, newest first.

    A researched row with no `source` column filled in is dropped rather than shown. On 17 September
    2026 this page carried a row saying Wanhua's Fuqing MDI expansion had completed, written from
    the plan's due date; two documents HTC holds say the timing was never stated and that the plant
    was still 800 kt a week before. Nothing reaches a reader now without naming who said it.
    """
    researched = []
    for row in _rows(directory, developments_file(chemical, geography)):
        attribution = str(row.get("source", "")).strip()
        if not attribution:
            continue
        researched.append(Development(
            date=date.fromisoformat(row["date"].strip()),
            precision=row.get("precision", "DAY").strip().upper() or "DAY",
            development=row["development"].strip(),
            type=row.get("type", "").strip(),
            source="researched",
            attribution=attribution,
            # Per-market file: every row in it was researched for this market.
            specific=True,
            context=str(row.get("context", "")).strip(),
            out_from=_date_or_none(row.get("out_from")),
            out_to=_date_or_none(row.get("out_to")),
            out_kt=_int(row.get("out_kt")),
        ))
    live = signal_developments(signals or [], graph=graph, chemical=chemical)
    written = register_developments(directory, chemical=chemical, geography=geography)
    seen: set[str] = set()
    merged: list[Development] = []
    for item in sorted(researched + live + written, key=lambda d: d.date, reverse=True):
        # Before the duplicate check, not after: two feeds carrying the same fact under different
        # sourcing are the same row once the sourcing is gone, and only then does the key match.
        shown = published_words(item)
        if shown is None:
            continue
        key = shown.development.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(shown)
    return merged


def format_development_date(item: Development) -> str:
    """As precisely as the source dated it, and never more precisely.

    A bare year sits badly beside "15 Sep 2026" and tells a reader almost nothing. Rows that could
    only be dated to a year came off the register on 17 September 2026 rather than being given a
    date nobody stated. QUARTER stays, because a source writing "targeted Q3 2026" has given a real
    answer.
    """
    if item.precision == "QUARTER":
        return f"Q{(item.date.month - 1) // 3 + 1} {item.date.year}"
    if item.precision == "PERIOD":
        # The week the report covered, which is a real answer. Printed as a bare year until
        # 22 October 2026, which is why such rows used to be kept off the register entirely.
        return f"week to {item.date.strftime('%d %b %Y')}"
    if item.precision == "YEAR":
        return str(item.date.year)
    if item.precision == "MONTH":
        return item.date.strftime("%b %Y")
    return item.date.strftime("%d %b %Y")


# ---------------------------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------------------------

SHORT_TERM_TYPES = frozenset({"Operations", "Feedstock", "Pricing"})
SHORT_TERM_WINDOW_DAYS = 120


@dataclass(frozen=True)
class Horizon:
    """One Impact line, and the evidence standing behind it.

    `as_of` and `count` were added on 5 November 2026. Stefan, looking at the page: "signals seem
    to stay the same". Two of the three lines he could see are arithmetic over a reviewed CSV and
    cannot change until somebody edits the file; the third stood on price letters seven weeks old.
    Nothing on the screen said so, so a stale conclusion and a fresh one looked identical. A line
    now carries the day of its newest evidence and how many items it rests on, and `impact` puts
    the freshest first.
    """
    label: str
    headline: str
    body: str
    quiet: bool
    as_of: date | None = None    # the newest item behind the line
    count: int = 0               # how many items it rests on
    unit: str = "signal"         # what those items ARE: the roster line counts plants, not signals
    standing: bool = False       # structure rather than news: the roster and the pipeline
    aging: str = ""              # set by `impact` where the evidence has gone quiet


def short_term(developments: list[Development], today: date | None = None) -> Horizon:
    """What the recent operational and feedstock record says about price and availability.

    "No market signals" is a finding, not a failure, and it is worded as one. The supporting line
    counts what WAS seen, so a quiet market is visibly quiet rather than indistinguishable from a
    collector that stopped working.
    """
    today = today or date.today()
    recent = [
        item for item in developments
        if item.type in SHORT_TERM_TYPES and (today - item.date).days <= SHORT_TERM_WINDOW_DAYS
    ]
    counts: dict[str, int] = {}
    for item in recent:
        counts[item.type] = counts.get(item.type, 0) + 1
    unplanned = [item for item in recent if "unplanned" in item.development.lower()
                 or "force majeure" in item.development.lower()]
    if unplanned:
        return Horizon(
            "Short term · price and availability",
            f"{len(unplanned)} unplanned {'stop' if len(unplanned) == 1 else 'stops'} in the last "
            f"{SHORT_TERM_WINDOW_DAYS // 30} months.",
            "; ".join(item.development for item in unplanned[:3]),
            quiet=False,
        )
    parts = []
    if counts.get("Operations"):
        parts.append(f"{counts['Operations']} scheduled "
                     f"{'turnaround' if counts['Operations'] == 1 else 'turnarounds'}")
    if counts.get("Feedstock"):
        parts.append(f"{counts['Feedstock']} feedstock price "
                     f"{'move' if counts['Feedstock'] == 1 else 'moves'}")
    if counts.get("Pricing"):
        parts.append(f"{counts['Pricing']} producer price "
                     f"{'action' if counts['Pricing'] == 1 else 'actions'}")
    body = ("Nothing recorded in the last four months."
            if not parts else
            f"In the last four months: {', '.join(parts)}.")
    return Horizon(
        "Short term · price and availability",
        "We have no market signals to expect any change in price or availability.",
        body, quiet=True,
    )


# Impact is a reaction to the signals (Stefan, 22 September 2026): three lines worked out from the
# rows in the table under it, each dated to them, and left out when there is nothing behind it.
PRICE_WINDOW_DAYS = 60
SUPPLY_AHEAD_DAYS = 90
FEEDSTOCK_WINDOW_DAYS = 30
_UP = re.compile(r"\b(rais|increas|hike|firm|tighten|climb|rally|rebound|up\b)", re.I)
_DOWN = re.compile(r"\b(lower|cut|decreas|reduc|soften|eas(?:e|ing|ed)|slip|down\b)", re.I)
_AMOUNT = re.compile(r"(EUR|USD|CNY|RMB|US\$)\s?([\d,]+)\s?/\s?t", re.I)


def _direction(text: str) -> int:
    up, down = bool(_UP.search(text or "")), bool(_DOWN.search(text or ""))
    return 1 if up and not down else -1 if down and not up else 0


def _who(item: Development) -> str:
    return item.development.split(" announced", 1)[0].split(" is reported", 1)[0].split(" raised", 1)[0] \
        .split(" lowered", 1)[0].split(" issued", 1)[0].strip()


def _short_date(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def price_line(developments: list[Development], today: date | None = None) -> Horizon | None:
    """Producers' own price actions on this chemical in the last 60 days."""
    today = today or date.today()
    recent = [d for d in developments
              if d.type == "Pricing" and 0 <= (today - d.date).days <= PRICE_WINDOW_DAYS]
    if not recent:
        return None
    ups = [d for d in recent if _direction(d.development) > 0]
    downs = [d for d in recent if _direction(d.development) < 0]
    since = _short_date(min(d.date for d in recent))
    if ups and not downs:
        headline = (f"Producers are pushing prices up: {len(ups)} "
                    f"{'increase' if len(ups) == 1 else 'increases'} announced since {since}.")
    elif downs and not ups:
        headline = (f"Producers are cutting prices: {len(downs)} "
                    f"{'cut' if len(downs) == 1 else 'cuts'} announced since {since}.")
    elif ups or downs:
        headline = f"Producer price actions are mixed since {since}: {len(ups)} up, {len(downs)} down."
    else:
        headline = (f"{len(recent)} producer price "
                    f"{'update' if len(recent) == 1 else 'updates'} since {since}, direction not stated.")
    parts = []
    for d in sorted(recent, key=lambda d: d.date, reverse=True)[:4]:
        amount = _AMOUNT.search(d.development)
        sign = "+" if _direction(d.development) > 0 else "-" if _direction(d.development) < 0 else ""
        what = f"{sign}{amount.group(1).upper()} {amount.group(2)}/t" if amount else "price update"
        parts.append(f"{_who(d)} {what} ({_short_date(d.date)})")
    return Horizon("Price", headline, "; ".join(parts) + ".", quiet=False,
                   as_of=max(d.date for d in recent), count=len(recent))


def supply_line(developments: list[Development], plants: list[Plant], geography: str = "China",
                today: date | None = None) -> Horizon | None:
    """Outages running now or due in the next three months, in kt and as a share of capacity."""
    today = today or date.today()
    horizon_end = today + timedelta(days=SUPPLY_AHEAD_DAYS)
    live = [d for d in developments if d.type == "Operations" and d.out_from
            and d.out_from <= horizon_end and (d.out_to or d.out_from) >= today]
    if not live:
        return None
    total = sum(p.kt for p in plants)
    kt = sum(d.out_kt for d in live if d.out_kt)
    share = f", {kt / total * 100:.0f}% of {_region(geography)} capacity of {total:,} kt" if kt and total else ""
    headline = (f"About {kt:,} kt of output is out now or due out in the next three months{share}."
                if kt else f"{len(live)} {'outage is' if len(live) == 1 else 'outages are'} "
                           "running now or due in the next three months.")
    body = " ".join(d.development + "." if not d.development.endswith(".") else d.development
                    for d in sorted(live, key=lambda d: d.out_from))
    return Horizon("Supply", headline, body, quiet=False,
                   as_of=max(d.date for d in live), count=len(live))


def feedstock_line(developments: list[Development], graph=None,
                   today: date | None = None) -> Horizon | None:
    """Which way this chemical's feedstocks moved in the last 30 days."""
    today = today or date.today()
    recent = [d for d in developments
              if d.type == "Feedstock" and 0 <= (today - d.date).days <= FEEDSTOCK_WINDOW_DAYS]
    if not recent:
        return None
    latest: dict[str, Development] = {}
    for d in sorted(recent, key=lambda d: d.date):
        if d.subject and _direction(d.development):
            latest[d.subject] = d

    def name(code: str) -> str:
        node = graph.node(_graph_code(graph, code)) if graph is not None else None
        return node.display_name if node else display_chemical(code)

    def listed(names: list[str]) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    ups = sorted({name(c) for c, d in latest.items() if _direction(d.development) > 0})
    downs = sorted({name(c) for c, d in latest.items() if _direction(d.development) < 0})
    if ups and not downs:
        headline = f"Feedstock costs are rising: {listed(ups)} last moved up."
    elif downs and not ups:
        headline = f"Feedstock costs are easing: {listed(downs)} last moved down."
    elif ups or downs:
        headline = f"Feedstock prices are mixed: {listed(ups)} up, {listed(downs)} down."
    else:
        headline = "Feedstock supply is changing."
    # The body carries what is not a price move: a feedstock plant closing, a supply change. A
    # producer's price update with no direction adds nothing to the line above it.
    events = [d for d in recent if not d.subject]
    body = " ".join(d.development.rstrip(".") + "." for d in events[:3])
    return Horizon("Feedstock", headline, body, quiet=False,
                   as_of=max(d.date for d in recent), count=len(recent))


# Added 5 November 2026 with the type translation above. Logistics is the largest class of signal
# the page holds - 56 of 122 items on TDI China the day it was counted - and it produced no line in
# Impact at all, because until that morning every one of those items reached the page untyped. The
# reader was told a price had moved in September and never told the ships had stopped in October.
LOGISTICS_WINDOW_DAYS = 45
MARKET_WINDOW_DAYS = 45


def logistics_line(developments: list[Development], condition=None,
                   today: date | None = None) -> Horizon | None:
    """What the shipping record says about getting the material here.

    The graded chain condition is the strongest statement the application holds about this, and it
    was rendered in its own card at the top of the page with nothing drawing on it. Where it is
    held it leads the line, and the dated incidents become the count behind it.
    """
    today = today or date.today()
    recent = [d for d in developments
              if d.type == "Logistics" and 0 <= (today - d.date).days <= LOGISTICS_WINDOW_DAYS]
    state = str(getattr(condition, "state", "") or "").strip()
    if not recent and not state:
        return None
    weeks = LOGISTICS_WINDOW_DAYS // 7
    if state:
        headline = (f"Freight is the live constraint: the chain is graded {state}"
                    + (f", on {len(recent)} {'report' if len(recent) == 1 else 'reports'} "
                       f"in the last {weeks} weeks." if recent else "."))
    else:
        headline = (f"{len(recent)} shipping {'report' if len(recent) == 1 else 'reports'} "
                    f"in the last {weeks} weeks bear on when material lands.")
    body = " ".join(d.development.rstrip(".") + "."
                    for d in sorted(recent, key=lambda d: d.date, reverse=True)[:3])
    dates = [d.date for d in recent]
    graded = _date_or_none(getattr(condition, "reported", None))
    if graded:
        dates.append(graded)
    return Horizon("Logistics", headline, body, quiet=False,
                   as_of=max(dates) if dates else None, count=len(recent))


def market_line(developments: list[Development], today: date | None = None) -> Horizon | None:
    """Where the market itself is, as HTC's own weekly reading of it put it.

    Distinct from Price above, which is producers' own announcements. A producer can post an
    increase into a market that is softening, and until these items were typed the page could show
    the letter and not the market.
    """
    today = today or date.today()
    recent = [d for d in developments
              if d.type == "Market" and 0 <= (today - d.date).days <= MARKET_WINDOW_DAYS]
    if not recent:
        return None
    newest = sorted(recent, key=lambda d: d.date, reverse=True)
    last = next((d for d in newest if _direction(d.development)), None)
    if last is None:
        # Nothing here says which way the market went, so there is no reading to report. This
        # used to print "2 market readings, direction not stated" over whatever had been typed
        # Market for want of another type - on 25 November 2026, a car-seat concept and a blowing
        # agent. Those are in the signals table under their own dates; they are not the market.
        return None
    else:
        way = "firmer" if _direction(last.development) > 0 else "softer"
        before = [d for d in newest if d.date < last.date and _direction(d.development)]
        against = sum(1 for d in before
                      if _direction(d.development) != _direction(last.development))
        turned = (f", against {against} earlier {'reading' if against == 1 else 'readings'} "
                  f"the other way" if against else "")
        headline = (f"The market was last read {way} on {_short_date(last.date)}{turned}.")
        # The line is as old as the reading it states, and it quotes readings. It used to take
        # its date from the newest item typed Market, whatever that item said: on 25 November 2026
        # it read "latest 15 Nov - the market was last read softer on 12 Oct", over a car-seat
        # concept and a blowing agent, because those two had no other type to fall under. Dated by
        # the reading, a line that old is left off the page by `current`.
        stated = [d for d in newest if _direction(d.development)]
        return Horizon("Market", headline,
                       " ".join(d.development.rstrip(".") + "." for d in stated[:3]),
                       quiet=False, as_of=last.date, count=len(stated))
    body = " ".join(d.development.rstrip(".") + "." for d in newest[:3])
    return Horizon("Market", headline, body, quiet=False,
                   as_of=newest[0].date, count=len(recent))


_ADJECTIVE = {"China": "Chinese", "Europe": "European", "North America": "North American"}

# What the plant table's second column is called. China keeps provinces; North America holds states
# and one Mexican site, so it is named for neither (Stefan, 23 September 2026).
_PLACE = {"China": "Province", "North America": "Location"}


def _region(geography: str) -> str:
    return _ADJECTIVE.get(str(geography or "China").strip(), str(geography or "").strip())


def medium_term(plants: list[Plant], geography: str = "China") -> Horizon | None:
    """Concentration, which is what decides whether a buyer has a choice of supplier.

    None where no roster is held. A card reading "No capacity roster is loaded" is a report on a
    file, which is HTC's business and not the reader's, and it was the second place on this page
    telling a customer about one.
    """
    shares = owner_shares(plants)
    total = sum(plant.kt for plant in plants)
    if not shares:
        return None
    top, top_kt, top_share, top_sites = shares[0]
    others = shares[1:4]
    small = [row for row in shares if row[1] < 100]
    body = (
        f"{top} controls {top_kt:,} of {total:,} kt across "
        f"{top_sites} {'site' if top_sites == 1 else 'sites'} - {top_share:.0f}%. "
        + ((f"The next owner has {others[0][2]:.0f}%. " if len(others) == 1 else
            f"The next {len(others)} owners have " + ", ".join(f"{row[2]:.0f}%" for row in others) + ". ")
           if others else "")
        + (f"{len(small)} of the {len(shares)} {'is' if len(small) == 1 else 'are'} under 100 kt."
           if small else "")
    ).strip()
    return Horizon(
        "Medium term · procurement position",
        f"One group holds {'well over half' if top_share > 55 else 'the largest share'} of {_region(geography)} capacity.",
        body, quiet=False, count=len(plants), unit="plant", standing=True,
    )


def long_term(plants: list[Plant], pipeline: list[PipelinePlant], geography: str = "China",
              chemical: str = "TDI") -> Horizon:
    """What is being built, which is what the market looks like after the current contracts."""
    if not pipeline:
        return Horizon("Long term · strategy", f"No new {_region(geography)} capacity is recorded as under way.",
                       "", quiet=True, standing=True)
    total = sum(plant.kt for plant in plants)
    incoming = sum(item.kt for item in pipeline)
    newcomers = [item for item in pipeline
                 if item.owner not in {plant.owner_group for plant in plants}]
    first = pipeline[0]
    growth = (incoming / total * 100) if total else 0
    # "Being built" only when every line is under construction; an announcement, an approval or a
    # published forecast is a plan, and the page says so.
    building = all(str(item.status).upper() == "UNDER_CONSTRUCTION" for item in pipeline)
    headline = (f"Another {incoming:,} kt is {'being built' if building else 'planned'}"
                + (f", by a company with no {display_chemical(chemical)} today." if newcomers else "."))
    due = f", due {first.due.strftime('%B %Y')}" if first.due else ""
    where = ", ".join(part for part in (first.site, first.province) if part)
    verb = "is building" if str(first.status).upper() == "UNDER_CONSTRUCTION" else "plans"
    sentences = [f"{first.owner} {verb} {first.kt:,} kt at {where}{due}."]
    if first.note:
        sentences.append(first.note[0].upper() + first.note[1:].rstrip(".") + ".")
    sentences.append(
        f"That is {growth:.0f}% more {_region(geography)} capacity"
        + (f" and a {_ordinal(len(plants_owners(plants)) + len(newcomers))} producer in the market."
           if newcomers else ".")
    )
    body = " ".join(sentences)
    return Horizon("Long term · strategy", headline, body, quiet=False,
                   count=len(pipeline), unit="project", standing=True)


def plants_owners(plants: list[Plant]) -> set[str]:
    return {plant.owner_group for plant in plants}


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


# How long a line may stand on its newest evidence before the page says so out loud.
STALE_AFTER_DAYS = 30


def impact(plants: list[Plant], pipeline: list[PipelinePlant], developments: list[Development],
           today: date | None = None, geography: str = "China", chemical: str = "TDI",
           graph=None, condition=None) -> list[Horizon]:
    """What the signals add up to, then the market's concentration and what is being added to it.

    Two orderings, not one. The lines that read signals come first and are sorted by the day of
    their newest evidence, so what moved this week sits above what moved in September. The two
    standing lines - who owns the capacity, what is being built - close the section, because they
    are the shape of the market rather than news about it and they change when a reviewed file
    changes, which is not weekly.

    A line whose newest evidence is older than `STALE_AFTER_DAYS` is marked as such. Before
    5 November 2026 a conclusion drawn from seven-week-old price letters was printed in the same
    type as one drawn from yesterday, which is how a page that was working looked like a page that
    had stopped.
    """
    today = today or date.today()
    dated = [price_line(developments, today),
             logistics_line(developments, condition, today),
             market_line(developments, today),
             supply_line(developments, plants, geography, today),
             feedstock_line(developments, graph, today)]
    dated = [horizon for horizon in dated if horizon is not None]
    dated.sort(key=lambda h: h.as_of or date.min, reverse=True)
    standing = [medium_term(plants, geography),
                long_term(plants, pipeline, geography, chemical)]
    out = []
    for horizon in dated + [h for h in standing if h is not None]:
        age = (today - horizon.as_of).days if horizon.as_of else None
        aging = f"nothing new in {age} days" if age is not None and age > STALE_AFTER_DAYS else ""
        out.append(horizon if not aging else replace(horizon, aging=aging))
    return out


def current(horizons: list[Horizon]) -> list[Horizon]:
    """The Impact lines a customer is shown: the ones that still stand on recent evidence.

    `impact` marks a line whose newest evidence is older than `STALE_AFTER_DAYS`, and until
    25 November 2026 the page printed it with that mark: "Market - latest 12 Oct - nothing new in
    44 days. The market was last read softer on 12 Oct." That is honest and it is not Impact. A
    reading that old is history, and under a heading that says what bears on the market now it
    told a customer the page had stopped (Stefan, the morning of a demo: "last message mid
    October. That is NOT good"). The line is left out; the signals it was drawn from stay in the
    tables below, each under its own date, where a six-week-old item reads as what it is.

    The standing lines - who owns the capacity, what is being built - carry no age and stay.
    """
    return [horizon for horizon in horizons if not horizon.aging]


# ---------------------------------------------------------------------------------------------
# The page itself.
#
# The markup is built here rather than in app.py so the page can be rendered and LOOKED AT without
# starting Streamlit. A layout that has only ever been described is not a layout that has been
# checked, and the last version of this page went out as a prototype nobody built.
# ---------------------------------------------------------------------------------------------

import html as _html

PAGE_CSS = """
<style>
.mi-sub{color:#5c7184;font-size:14px;margin:-6px 0 18px}
.mi-card{background:#fff;border:1px solid #dde5ec;border-radius:8px;padding:18px 20px;margin-bottom:14px}
.mi-kv{display:flex;gap:34px;flex-wrap:wrap}
.mi-kv>div{min-width:120px}
.mi-kv .big{font-size:24px;font-weight:700;letter-spacing:-.4px;color:#12212f}
.mi-kv .lbl{color:#5c7184;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.mi-card table{width:100%;border-collapse:collapse;font-size:14px}
.mi-card th{text-align:left;font-weight:600;color:#5c7184;font-size:12px;text-transform:uppercase;
  letter-spacing:.4px;border-bottom:1px solid #dde5ec;padding:0 10px 7px 0}
.mi-card td{padding:7px 10px 7px 0;border-bottom:1px solid #eef2f6;vertical-align:top;color:#12212f}
.mi-card td.n,.mi-card th.n{text-align:right;font-variant-numeric:tabular-nums}
.mi-card tr.total td{font-weight:700;border-top:2px solid #dde5ec;border-bottom:none}
.mi-bar{height:9px;background:#eaf0f6;border-radius:5px;overflow:hidden;min-width:110px}
.mi-bar span{display:block;height:100%;background:#1d4ed8}
.mi-chg{color:#b45309;font-weight:600}
.mi-date{white-space:nowrap;color:#5c7184;font-variant-numeric:tabular-nums}
.mi-tag{display:inline-block;font-size:11px;letter-spacing:.3px;text-transform:uppercase;
  border:1px solid #dde5ec;border-radius:3px;padding:1px 6px;color:#5c7184;white-space:nowrap}
.mi-legend{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px 22px;font-size:13px}
.mi-legend div{color:#5c7184}
.mi-legend b{color:#12212f;font-weight:600;font-size:11px;letter-spacing:.3px;text-transform:uppercase;
  border:1px solid #dde5ec;border-radius:3px;padding:1px 6px;margin-right:7px;white-space:nowrap}
.mi-ctx{font-size:13px;color:#5c7184;margin-top:3px}
.mi-unconfirmed{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.7px;
text-transform:uppercase;color:#8a5a00;background:#fdf3dc;border:1px solid #f0ddb0;
border-radius:3px;padding:1px 6px;margin-right:7px;vertical-align:1px}
.mi-cond-state{display:inline-block;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#fff;background:#b4532f;border-radius:3px;padding:2px 8px}
.mi-cond-what{font-size:15px;font-weight:600;color:#1f3b52;margin-top:7px}
.mi-cond-day{font-weight:400;color:#5c7184;font-size:13px}
.mi-level{white-space:nowrap;font-weight:600}
.mi-do{font-size:13px;color:#1f3b52;margin-top:5px;padding-left:9px;border-left:2px solid #c6d4de}
.mi-note{font-size:13px;color:#5c7184;background:#eef3f8;border:1px solid #dde5ec;border-radius:6px;
  padding:12px 14px;margin:14px 0 4px}
.mi-horizon{border-left:3px solid #1d4ed8;padding-left:16px;margin:16px 0 22px}
.mi-horizon .label{color:#5c7184;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.mi-horizon h3{margin:0 0 4px;font-size:15.5px;color:#12212f}
.mi-horizon p{margin:0;color:#12212f}
.mi-quiet{border-left-color:#c8d3dd}
.mi-quiet h3{color:#5c7184;font-weight:600}
.mi-aging{border-left-color:#d9a441}
.mi-horizon .label .mi-when{text-transform:none;letter-spacing:0;color:#7b8ea0}
.mi-horizon .label .mi-stale{text-transform:none;letter-spacing:0;color:#b45309;font-weight:600}
.mi-horizon .label .mi-sep{color:#9fb0c0;font-weight:400;margin:0 7px}
.mi-cap{color:#5c7184;font-size:13px;margin:0 0 11px}
.mi-run td{color:#7b8ea0;font-style:italic;background:#fafcfd}
</style>
"""


def _escape(value: object) -> str:
    return _html.escape(str(value if value is not None else ""))


def headline_block(plants: list[Plant], pipeline: list[PipelinePlant],
                   geography: str = DEFAULT_MARKET[1]) -> str:
    cells = "".join(
        f'<div><div class="lbl">{_escape(label)}</div><div class="big">{_escape(value)}</div></div>'
        for label, value in headline_figures(plants, pipeline, geography).items()
    )
    return f'<div class="mi-card"><div class="mi-kv">{cells}</div></div>'


def owner_block(plants: list[Plant]) -> str:
    shares = owner_shares(plants)
    largest = shares[0][1] if shares else 0
    rows = "".join(
        f"<tr><td>{_escape(owner)}"
        + (f' <span class="mi-tag">{sites} sites</span>' if sites > 1 else "")
        + f'</td><td class="n">{kt:,}</td><td class="n">{share:.1f}%</td>'
        + '<td><div class="mi-bar"><span style="width:'
        + f'{(kt / largest * 100) if largest else 0:.1f}%"></span></div></td></tr>'
        for owner, kt, share, sites in shares
    )
    total = sum(plant.kt for plant in plants)
    return (
        '<div class="mi-card"><table><thead><tr><th>Owner</th><th class="n">kt/yr</th>'
        '<th class="n">Share</th><th style="width:34%"></th></tr></thead><tbody>'
        + rows
        + f'<tr class="total"><td>Total</td><td class="n">{total:,}</td>'
          '<td class="n">100%</td><td></td></tr></tbody></table></div>'
    )


def _plant_note(plant: Plant) -> str:
    """What the plant is today. Nothing about how we arrived at the number, and nothing about the past.

    The first version printed our own reconciliation here - "revised up from 250", "counted twice in
    the source", "company interim report". That is bookkeeping: it tells the reader how we did our
    filing, not what is happening in their market, and it makes them read three words of ours for
    every one of theirs. It stays in the file, in `internal_note`, where it belongs.

    The second version printed what had already happened - "second line on stream Aug 2025",
    "debottlenecked Oct 2024". The capacity in the kt column is the outcome of those events, so the
    note said the same thing twice and dated it. What a plant did in 2024 is not a reason to read
    this table. Past events belong in the developments register, where they carry a date and can be
    read in order.
    """
    return f'<span class="mi-chg">{_escape(plant.note)}</span>' if plant.note else ""


def plant_block(plants: list[Plant], geography: str = "China") -> str:
    # China's plants are placed by province, Europe's by country.
    place = _PLACE.get(str(geography or "China").strip(), "Country")
    # No column for something no plant has to say. A Note header over eleven empty cells is a
    # quarter of the table's width spent on nothing.
    noted = any(plant.note for plant in plants)
    rows = "".join(
        f"<tr><td>{_escape(plant.plant)}</td><td>{_escape(plant.province)}</td>"
        f'<td class="n">{plant.kt:,}</td>'
        + (f"<td>{_plant_note(plant)}</td>" if noted else "")
        + "</tr>"
        for plant in plants
    )
    head = (f'<tr><th>Plant</th><th>{place}</th><th class="n">kt/yr</th>'
            + ("<th>Note</th>" if noted else "") + "</tr>")
    return ('<div class="mi-card"><table><thead>' + head + "</thead><tbody>" + rows
            + "</tbody></table></div>")


def run_row(run: Run) -> str:
    """The stand-in row. It counts and it dates, and it does not characterise."""
    # Plain words, not a thin space and an en dash. This column is read rather than admired, and
    # the application has been caught once already by an exotic character in a rendered string.
    span = f"{run.oldest.strftime('%d %b')} to {run.newest.strftime('%d %b %Y')}"
    more = "signal" if run.count == 1 else "signals"
    return (f'<tr class="mi-run"><td class="mi-date">{_escape(span)}</td>'
            f"<td>{run.count} more {_escape(run.type)} {more}, "
            "behind the fold below.</td></tr>")


def developments_block(developments: list, caption: str = "",
                       empty: str = "Nothing recorded yet.") -> str:
    if not developments:
        return ('<div class="mi-card"><div class="mi-note" style="margin:0">'
                f"{_escape(empty)}</div></div>")
    # No Source column. Nearly every row reads "Source not disclosed", which is a column of one
    # repeated phrase taking a fifth of the table's width and telling a reader nothing they can use.
    # The attribution is still required and still recorded - a row that cannot name where it came
    # from is dropped in `load_developments` and never gets this far.
    # The implication is the one line a reader can act on - cover now, hold, re-quote, re-check the
    # contract formula. Clint Raine to Stefan, 15 October 2026, on what a buyer of these reports is
    # actually buying: "they do need evidence". The evidence is the event; the implication is what
    # it is evidence FOR. It only ever comes from HTC's own writing, so most rows have none.
    # An unconfirmed item is printed - a stated indication is worth having - but it is not printed
    # as a fact. Until 6 November 2026 the register's own Status line was read, used to decide
    # whether the row could be published at all, and then discarded, so "distributor weekly
    # guidance, claimed channel evidence" sat in the same type as a confirmed sinking.
    rows = "".join(
        run_row(item) if isinstance(item, Run) else
        f'<tr><td class="mi-date">{_escape(format_development_date(item))}</td>'
        + "<td>"
        # The word is the briefing's own. An item whose Status reads "Market indication based on
        # Longzhong national mainstream pricing" is a published reading, not a rumour, and it was
        # being printed under "Unconfirmed" - which is a different grade, and one this page does
        # not print at all (`intelligence_register.PRINTABLE`). Stefan, 25 November 2026, on a
        # page about to be shown to a customer: "it says things like unconfirmed. That is NOT good."
        + ('<span class="mi-unconfirmed">Indication</span>'
           if str(getattr(item, "confidence", "")).upper() == "INDICATION" else "")
        + f"{_escape(item.development)}"
        + (f'<div class="mi-ctx">{_escape(item.context)}</div>' if item.context else "")
        + (f'<div class="mi-do">{_escape(item.implication)}</div>'
           if getattr(item, "implication", "") else "")
        + "</td></tr>"
        for item in developments
    )
    # No Type column (Stefan, 22 September 2026): a tag nobody acts on. The type is still recorded;
    # Impact above the table is worked out from it.
    head = f'<div class="mi-cap">{_escape(caption)}</div>' if caption else ""
    return ('<div class="mi-card">' + head + '<table><thead><tr><th>Date</th><th>Signal</th>'
            "</tr></thead><tbody>" + rows + "</tbody></table></div>")


CONDITION_HEADING = "### Supply Chain Condition"


def latest_condition(directory: Path = DATA_DIR):
    """The newest grading of the chain itself, or None where none has been imported."""
    from raw_material_forecasting import supply_conditions

    held = [c for c in supply_conditions.load(directory) if c.reported]
    return max(held, key=lambda c: c.reported) if held else None


def condition_block(condition, chemical: str = DEFAULT_MARKET[0]) -> str:
    """The state of the chain, and what this market's inputs are exposed to.

    Nine Strait of Hormuz special reports had been imported by 12 October 2026 - the chain graded
    Disrupted, six input groups graded with the mechanism behind each - and no page in the
    application read them. A reader was told a price moved and never learned the ships had stopped.

    An exposure that names no chemical is shown on every market: catalysts, surfactants and
    packaging are graded High and Medium here and they are inputs to all of it. Stefan, 16 October
    2026: "bind to chemical and geography sure but dont limit its context."
    """
    if condition is None:
        return ""
    mine = exposures_for(condition, chemical)
    when = format_day(condition.reported)
    head = (f'<div class="mi-cond-state">{_escape(condition.state)}</div>'
            f'<div class="mi-cond-what">{_escape(condition.event)}'
            f'<span class="mi-cond-day"> &nbsp;·&nbsp; {_escape(when)}</span></div>')
    note = (f'<div class="mi-ctx">{_escape(condition.note)}</div>' if condition.note else "")
    rows = "".join(
        f'<tr><td>{_escape(e.group)}</td><td class="mi-level">{_escape(e.level)}</td>'
        f"<td>{_escape(e.mechanism)}</td></tr>"
        for e in mine)
    table = ('<table><thead><tr><th>Input</th><th>Exposure</th><th>Through what</th></tr></thead>'
             f"<tbody>{rows}</tbody></table>" if rows else "")
    return f'<div class="mi-card">{head}{note}{table}</div>'


def exposures_for(condition, chemical: str = DEFAULT_MARKET[0]) -> list:
    """This market's own input group, plus every group that names no chemical.

    One rule, used by the screen and by the Word report, so the two cannot say different things.
    Catalysts, surfactants, additives and packaging name no chemical because they are inputs to all
    of it, and they are graded High and Medium on the 12 October 2026 reading.
    """
    if condition is None:
        return []
    code = str(chemical or "").strip().upper()
    out = []
    for exposure in condition.exposures:
        named = [c.strip().upper() for c in str(exposure.chemicals or "").split(";") if c.strip()]
        if not named or code in named:
            out.append(exposure)
    return out


def format_day(stamp: str) -> str:
    """An ISO day as the page writes days. Returns the text unchanged where it is not one."""
    try:
        return date.fromisoformat(str(stamp)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(stamp or "")


def legend_block(chemical: str = DEFAULT_MARKET[0]) -> str:
    # The Feedstock line names what THIS chemical is made from. It read "a material TDI is made
    # from" on every market, which on a polyol page is simply wrong.
    made_from = FEEDSTOCKS.get(str(chemical or "").strip().upper())
    entries = [
        (label, f"A material {display_chemical(chemical)} is made from: {made_from}."
                if label == "Feedstock" and made_from else meaning)
        for label, meaning in TYPE_LEGEND
    ]
    legend = "".join(f"<div><b>{_escape(label)}</b>{_escape(meaning)}</div>"
                     for label, meaning in entries)
    return ('<div class="mi-card"><div style="color:#5c7184;font-size:12px;text-transform:uppercase;'
            'letter-spacing:.4px;margin-bottom:9px">Type</div>'
            f'<div class="mi-legend">{legend}</div></div>')


def horizon_meta(horizon: Horizon) -> str:
    """How much is behind this line and how recent it is, as plain words.

    Built here and used by the screen and by the Word report, so a reader of the document is told
    the same thing as a reader of the page.
    """
    bits = []
    if horizon.count:
        unit = horizon.unit or "signal"
        bits.append(f"{horizon.count} {unit}" if horizon.count == 1
                    else f"{horizon.count} {unit}s")
    if horizon.as_of:
        bits.append(f"latest {horizon.as_of.strftime('%d %b')}")
    if horizon.standing:
        bits.append("standing position, moves when the roster does")
    return " \u00b7 ".join(bits)


def horizon_block(horizon: Horizon) -> str:
    """One Impact line with its evidence stated beside it.

    The accent down the left says which of three things this is at a glance: live (blue), gone
    quiet (amber), or structure that was never going to move this week (grey).
    """
    accent = (" mi-quiet" if horizon.quiet or horizon.standing else
              " mi-aging" if horizon.aging else "")
    meta = horizon_meta(horizon)
    # The separator is a character in the markup, not a CSS `content:` escape. PAGE_CSS is an
    # ordinary triple-quoted Python string, so a CSS escape written as "\\00b7" is read by PYTHON
    # first - \\00 is an octal NUL - and the rule shipped a null byte followed by the letters b7.
    # Caught on the running page, 5 November 2026. What has to be a character belongs where it is
    # one.
    sep = '<span class="mi-sep">·</span>'
    label = (f'<div class="label">{_escape(horizon.label)}'
             + (f'{sep}<span class="mi-when">{_escape(meta)}</span>' if meta else "")
             + (f'{sep}<span class="mi-stale">{_escape(horizon.aging)}</span>'
                if horizon.aging else "")
             + "</div>")
    return (f'<div class="mi-horizon{accent}">{label}'
            f"<h3>{_escape(horizon.headline)}</h3>"
            + (f"<p>{_escape(horizon.body)}</p>" if horizon.body else "")
            + "</div>")


# The table's heading says what the figures are, so the caption underneath saying the same thing
# went. Nameplate is the standard basis for a capacity roster; naming it once, above the numbers,
# is enough.
CAPACITY_HEADING = "### Nameplate Capacity"

# The dated signals had a table and no heading of their own, so the section read as one long list
# under Impact with nothing saying where the conclusion stopped and the evidence began (Stefan,
# 25 September 2026).
SIGNALS_HEADING = "#### Earlier Signals"

# What counts as new enough to be shown without a click. Stefan, 5 November 2026, on the page as it
# stood: "details change but do not show unless unfolded." Every dated signal was behind the one
# fold, so the only part of the page a reader saw without clicking was the part that cannot change
# week to week. The recent signals now stand open and the back catalogue keeps the fold.
#
# The floor matters on a quiet market: a window alone would show an empty card in a week when
# nothing landed, which reads as a broken page rather than a quiet one.
LATEST_HEADING = "#### Latest Signals"
LATEST_WINDOW_DAYS = 30
LATEST_MINIMUM = 6
# The floor on signals naming this market's own chemical, whatever their age. Three is enough to
# be worth the heading and small enough that a quiet market does not push out a live constraint.
OWN_MARKET_MINIMUM = 3
# A class of signal only collapses once it is bigger than this, and two of it stay visible when it
# does. Four is the point at which a reader stops reading the rows and starts scrolling past them.
RUN_LIMIT = 4
RUN_LEAD = 2


def split_developments(developments: list[Development],
                       today: date | None = None) -> tuple[list[Development], list[Development]]:
    """The signals shown open, and the ones behind the fold.

    `developments` arrives newest first.

    Two decisions, and they are separate. HOW MANY rows stand open is the last 30 days, never
    fewer than six - unchanged since v1.2.68. WHICH rows fill that space now leads with the
    signals that name this market.

    Stefan, 6 November 2026: eleven of the fourteen rows standing open on TDI China were maritime,
    and the same eleven stood open on pMDI China and Polyether Polyol China. A register row that
    names no chemical is scoped worldwide and lands on every page, which is right - freight
    through Hormuz reaches all of them - but it meant a reader who opened TDI China got one row
    about TDI, seventh down, under six about ships.

    Naming the CHEMICAL is the test, not naming the region: the rows scoped "Asia" and
    "Worldwide" on that page are themselves the shipping news, so ranking a region above the
    world would promote the very thing this exists to demote.

    `OWN_MARKET_MINIMUM` is the floor. Without it a market that produces one signal a month shows
    a page of world news under its own name; with it, the promoted rows push the general ones into
    the fold and the table stays the length it was.
    """
    today = today or date.today()
    recent = sum(1 for item in developments if (today - item.date).days <= LATEST_WINDOW_DAYS)
    cut = min(max(recent, LATEST_MINIMUM), len(developments))

    own = [item for item in developments if item.specific]
    chosen: list[Development] = []
    seen: set[int] = set()
    for item in own[:OWN_MARKET_MINIMUM] + developments[:cut]:
        if id(item) not in seen:
            seen.add(id(item))
            chosen.append(item)
    chosen = chosen[:cut] if cut else []

    # This market's own, newest first; then the wider ones, newest first.
    latest = sorted(chosen, key=lambda item: (not item.specific, -item.date.toordinal()))
    kept = {id(item) for item in latest}
    # `developments` is already newest first, so the fold keeps plain date order.
    earlier = [item for item in developments if id(item) not in kept]
    return latest, earlier

def collapse_runs(developments: list[Development]) -> tuple[list, list[Development]]:
    """The open table with a crowded class standing as one row, and the rows it stood in for.

    Returns `(shown, pushed)`. `shown` holds `Development` and `Run` together, in the order the
    table draws them; `pushed` is what moved to the fold and must be added to it, because the
    whole record is still held between the two tables.

    This market's own signals never collapse, whatever their number. They are what the page is
    for, and a reader who came for TDI did not come to be told there were six TDI signals.
    """
    classes: dict[str, list[Development]] = {}
    for item in developments:
        if not item.specific:
            classes.setdefault(item.type, []).append(item)

    pushed: dict[int, Development] = {}
    stands: dict[int, Run] = {}
    for kind, items in classes.items():
        if len(items) <= RUN_LIMIT:
            continue
        replaced = items[RUN_LEAD:]
        for item in replaced:
            pushed[id(item)] = item
        dates = [item.date for item in replaced]
        # The Run takes the place of the first row it replaced, so the table keeps its order.
        stands[id(replaced[0])] = Run(type=kind, count=len(replaced),
                                      oldest=min(dates), newest=max(dates))

    shown: list = []
    for item in developments:
        if id(item) in stands:
            shown.append(stands[id(item)])
        elif id(item) not in pushed:
            shown.append(item)
    return shown, list(pushed.values())


# The two long tables on the page open on a click rather than on arrival. A reader comes to this
# page for the market, and thirty plant rows above the signals push everything else off the screen.
# Heading block -> the words on the fold (Stefan, 25 September 2026).
#
# The Word report keeps both tables open: paper has no fold.
FOLDED: dict[str, str] = {
    CAPACITY_HEADING: "Nameplate Capacity",
    SIGNALS_HEADING: "Earlier Signals",
}


def latest_caption(latest: list, earlier: list[Development],
                   today: date | None = None) -> str:
    """What the open table covers, in what order, and how much is still folded under it.

    The order is now worth a sentence. A reader who sees three TDI rows above eleven about
    shipping should be told that is the arrangement and not the chronology.
    """
    if not latest:
        return ""
    own = sum(1 for item in latest if getattr(item, "specific", False))
    if own:
        lead = (f"This market's own {own} "
                f"{'signal' if own == 1 else 'signals'} first, then the wider ones, "
                f"newest of each at the top")
    else:
        lead = ("Nothing held names this market directly, so these are the wider signals, "
                "newest first")
    if not earlier:
        return f"{lead}. Nothing else is held for this market."
    return (f"{lead}. {len(earlier)} earlier "
            f"{'signal is' if len(earlier) == 1 else 'signals are'} behind the fold below.")


def page_blocks(plants: list[Plant], pipeline: list[PipelinePlant], signals=None,
                today: date | None = None, *, chemical: str = DEFAULT_MARKET[0],
                geography: str = DEFAULT_MARKET[1],
                directory: Path = DATA_DIR, graph=None) -> list[str]:
    """Every block of the page in order, ready to be written out.

    The whole page takes the market now, not only the plant roster: the heading, the capacity
    figure, the Feedstock line of the legend, and which developments file is read. It said
    "TDI · China" whatever was selected.
    """
    developments = load_developments(directory, signals=signals, chemical=chemical,
                                     geography=geography, graph=graph)
    today = today or date.today()
    blocks = [
        PAGE_CSS,
        f"## {display_chemical(chemical)} · {geography}",
        f'<div class="mi-sub">Market Intelligence &nbsp;·&nbsp; {today.strftime("%d %B %Y")}</div>',
    ]
    # The heading goes only where there is something under it. "Current Market Situation" over one
    # sentence saying no roster is loaded is a loading state dressed as a section, on a screen a
    # customer reads.
    condition = latest_condition(directory)
    if condition is not None:
        blocks += [CONDITION_HEADING, condition_block(condition, chemical)]
    if plants:
        blocks += ["### Current Market Situation",
                   headline_block(plants, pipeline, geography), owner_block(plants),
                   CAPACITY_HEADING, plant_block(plants, geography)]
    # Impact sits at the top of Market Signals, above the table it is worked out from, so a reader
    # gets the conclusion first and the detail underneath (Stefan, 22 September 2026). No Type legend.
    blocks += ["### Market Signals", "#### Impact"]
    blocks += [horizon_block(horizon)
               for horizon in current(impact(plants, pipeline, developments, today,
                                             geography, chemical, graph, condition))]
    # The recent signals stand open under Impact; everything older keeps the fold. The conclusion
    # is still first and the evidence for it is now under it without a click.
    latest, earlier = split_developments(developments, today)
    # Collapse AFTER promoting, so a crowded class is thinned and this market's own rows are not:
    # `collapse_runs` reads `specific`, which is the same answer the promotion was sorted on.
    shown, pushed = collapse_runs(latest)
    earlier = sorted(pushed + earlier, key=lambda item: item.date, reverse=True)
    blocks += [LATEST_HEADING,
               developments_block(shown, latest_caption(shown, earlier, today),
                                  empty="No signal has been collected for this market yet.")]
    blocks += [SIGNALS_HEADING,
               developments_block(earlier,
                                  empty="Everything held for this market is shown above.")]
    blocks += [SIGNALS_ARE_WORLDWIDE]
    return blocks
