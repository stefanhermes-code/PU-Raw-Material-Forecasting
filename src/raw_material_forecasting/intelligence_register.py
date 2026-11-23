"""The register of what happened, beside the register of what things cost.

One file, one row per briefing item, written by the Import page and read by the report. It is kept
apart from the per-market developments files, which are maintained by hand for HTC's own Market
Intelligence page: a briefing item names several markets at once, and copying one row into fourteen
files is how two copies of the same fact end up disagreeing.

Three columns decide whether a row reaches a customer. `confidence` is the briefing's own Status
line, and only a confirmed item or a stated indication is ever printed. `chemicals` and
`geographies` are the markets the item is about, with Worldwide and Asia kept as what they are
rather than narrowed to whichever region matched a word. And `reported` is the day the briefing
carried it, which is the day it moved sentiment, whatever day the event itself happened.
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from raw_material_forecasting.briefing_reader import ASIA, CONFIRMED, INDICATION, IN_ASIA, WORLDWIDE

REGISTER = "briefing_register.csv"
# `document_sha` is appended rather than placed beside `document`, so a register written before
# 16 October 2026 reads unchanged: a missing column loads as empty.
COLUMNS = ("reported", "event_date", "precision", "headline", "kind", "chemicals", "geographies",
           "confidence", "context", "implication", "quote", "attribution", "document", "stored_on",
           "document_sha")
PRINTABLE = (CONFIRMED, INDICATION)

# Nobody is named in a customer's report. Stefan, 8 October 2026: it is HTC Global's own research
# and no source appears anywhere in it.
#
# These are the price assessors and the news desks a briefing cites. Producers are deliberately not
# in this list: BASF announcing its own increase and Wanhua restarting its own plant are the events
# themselves, not somebody's reporting of a price, and naming them is naming what happened.
NAMED_SOURCES = (
    "ICIS", "PUdaily", "PU Daily", "SunSirs", "Mysteel", "Longzhong", "Reuters", "Caixin",
    "ChemNet", "Business Society", "PU Magazine", "Argus", "Platts", "S&P Global", "Bloomberg",
    "Sina Finance", "ECHEMI", "Chemical Week", "Tecnon", "Nexant", "Kpler", "TankerTrackers",
    # Added 6 November 2026. "Secondary SunSirs PriceSeek report: ..." was the headline of a row
    # on the Market Intelligence page: the list caught SunSirs and had never heard of the other.
    # These are the rest of the services a briefing cites - price assessors, index houses and the
    # market-research publishers whose summaries reach us.
    "PriceSeek", "SCI99", "ChemAnalyst", "ChemOrbis", "IMARC", "Fastmarkets", "OPIS", "Baiinfo",
    "Zhuochuang", "CCF Group", "Polymerupdate", "IHS Markit", "Wood Mackenzie", "WoodMac",
    "Business Analytiq", "Intratec", "GlobalData", "MarketsandMarkets", "Mordor Intelligence",
    "Grand View Research", "Expert Market Research", "Trading Economics", "Statista",
)


def _source_pattern(name: str) -> "re.Pattern[str]":
    body = r"\s+".join(re.escape(part) for part in name.split())
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.I)


_NAMED = tuple((name, _source_pattern(name)) for name in NAMED_SOURCES)


def names_a_source(*texts: str) -> tuple[str, ...]:
    """Which assessors or news desks are named in text a customer would read.

    Whole words. Written as `name.lower() in joined` until 6 November 2026, which is the same
    substring fault the page's own classifier carried until the day before: a short name matches
    inside a longer word, and a false positive here takes a row off a customer's page.
    """
    joined = " ".join(str(part or "") for part in texts)
    return tuple(name for name, pattern in _NAMED if pattern.search(joined))


# Prose that points at a DOCUMENT rather than at the market. `names_a_source` cannot catch this
# shape, because it names nobody: "the earlier source set showed 143 transits", "no named producer
# in the report". Both were on the Market Intelligence page on 6 November 2026 - Stefan, looking at
# Latest Signals: "Make sure these market signals do not leak sources."
_SOURCE_OPENERS: tuple["re.Pattern[str]", ...] = (
    # "The earlier source set showed 143 transits ..." -> "143 transits ..."
    re.compile(r"^\s*(?:the\s+)?(?:earlier|later|latest|newer|new|first|second|same|other)?\s*"
               r"source\s+set\s+(?:showed|shows|gave|gives|had|has|reported)\s+", re.I),
    # "Secondary SunSirs PriceSeek report: plant operating disturbance ..." -> "Plant operating ..."
    # The service names leave with the clause that holds them, which is why `NAMED_SOURCES` is the
    # backstop here and not the first line of defence.
    re.compile(r"^\s*(?:a\s+|the\s+)?secondary\s+[^:]{0,80}?\breports?:\s*", re.I),
)
_SOURCE_PHRASES: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"\bin (?:the|this|that) report\b", re.I), "behind it"),
    (re.compile(r"\b(?:the|this) report (?:says|states|claims|notes)\b", re.I), "it is claimed"),
    (re.compile(r"\b(?:our|the)\s+(?:earlier\s+)?source set\b", re.I), "the record"),
)


def describes_its_sourcing(text: str) -> bool:
    """Does this line point at a DOCUMENT rather than at the market?

    Only the source-shaped patterns, which is why this is not "`source_free` would change it".
    That function also tidies whitespace and the opening capital, so a page footer reading
    "Q3 2026   |   Page 1" changes under it while describing nothing at all - a false positive
    that failed a clean report the first time the gate was written this way.
    """
    value = str(text or "")
    return (any(pattern.search(value) for pattern in _SOURCE_OPENERS)
            or any(pattern.search(value) for pattern, _ in _SOURCE_PHRASES))


def source_free(text: str) -> str:
    """One prose field with the sourcing taken out of it.

    Who says so is recorded and never printed: `attribution` holds it, and the Market Intelligence
    page took its Source column off the table on 22 September 2026. The words then smuggled it
    back in, which is what this undoes.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    for pattern in _SOURCE_OPENERS:
        cleaned = pattern.sub("", cleaned, count=1)
    for pattern, replacement in _SOURCE_PHRASES:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ;,")
    # A stripped opener leaves the sentence starting on what used to be mid-sentence.
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


@dataclass
class Entry:
    """One thing that happened, and how far a reader may lean on it."""

    reported: str = ""
    event_date: str = ""
    precision: str = "DAY"
    headline: str = ""
    kind: str = ""
    chemicals: str = ""        # semicolon separated, in our own codes
    geographies: str = ""      # semicolon separated, Worldwide and Asia included as themselves
    confidence: str = ""
    context: str = ""
    implication: str = ""
    quote: str = ""
    attribution: str = ""      # who said it, kept off every customer page
    document: str = ""         # the file name it arrived under, which can change between weeks
    stored_on: str = ""
    document_sha: str = ""     # the SHA-256 of that document's bytes: which document, by content

    @property
    def chemical_list(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.chemicals.split(";") if part.strip())

    @property
    def geography_list(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.geographies.split(";") if part.strip())

    @property
    def day(self) -> date | None:
        for value in (self.event_date, self.reported):
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
        return None

    @property
    def reported_on(self) -> date | None:
        try:
            return date.fromisoformat(str(self.reported)[:10])
        except ValueError:
            return None

    @property
    def is_printable(self) -> bool:
        return self.confidence in PRINTABLE

    @property
    def key(self) -> tuple[str, str]:
        """What makes two rows the same row: the same statement out of the same document.

        Which document is answered by content where we have it. Keyed on the file name, the same
        weekly report arriving with a hyphen added to its name would import every item a second
        time, and a document rewritten under the name it already had would dedupe against rows it
        no longer contains. The file name is the fallback for rows written before the store existed
        (Stefan, 16 October 2026: judging by title "is dangerous").
        """
        which = self.document_sha.strip().lower() or self.document.strip().lower()
        return (which, self.headline.strip().lower())


def path(directory: Path) -> Path:
    return Path(directory) / REGISTER


def load(directory: Path) -> list[Entry]:
    file = path(directory)
    if not file.exists():
        return []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        return [Entry(**{column: (row.get(column) or "").strip() for column in COLUMNS})
                for row in csv.DictReader(handle)
                if any((value or "").strip() for value in row.values())]


def save(entries: Sequence[Entry], directory: Path) -> Path:
    file = path(directory)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for entry in entries:
            writer.writerow({column: getattr(entry, column) for column in COLUMNS})
    return file


def append(new: Iterable[Entry], directory: Path) -> tuple[int, int]:
    """Add what is not there already. The same briefing imported twice stores nothing twice."""
    held = load(directory)
    seen = {entry.key for entry in held}
    added = 0
    for entry in new:
        if entry.key in seen:
            continue
        seen.add(entry.key)
        held.append(entry)
        added += 1
    save(sorted(held, key=lambda row: (row.reported, row.headline)), directory)
    return added, len(held)


def covers(entry: Entry, *, chemical: str, geography: str) -> bool:
    """Whether this row is about that market.

    Worldwide covers every market, and Asia covers the Asian ones. Neither is narrowed: an item
    about Asian isocyanate supply belongs under Chinese pMDI even though it never said China.

    An EMPTY list means every one, on both axes. Binding attaches an item to markets; it never
    restricts what the item says, and an item that names no chemical is not an item about nothing -
    it is an item about all of them. Stefan, 16 October 2026: "bind to chemical and geography sure
    but dont limit its context." The Strait of Hormuz is the case that proves it: six ships struck
    in a day bears on every isocyanate and every polyol that moves through the Gulf, and it names
    none of them. Read the other way round - empty meaning nothing - the single largest supply
    event of the quarter appears on no page at all.
    """
    what = entry.chemical_list
    if chemical and what and chemical not in what:
        return False
    where = entry.geography_list
    if not where:
        return True
    if WORLDWIDE in where:
        return True
    if geography in where:
        return True
    return ASIA in where and geography in IN_ASIA


def for_market(entries: Sequence[Entry], *, chemical: str, geography: str,
               first_day: date | None = None, last_day: date | None = None,
               printable_only: bool = True) -> list[Entry]:
    """What was behind this market over this period, most recent first."""
    out = []
    for entry in entries:
        if printable_only and not entry.is_printable:
            continue
        if not covers(entry, chemical=chemical, geography=geography):
            continue
        when = entry.reported_on
        if when is None:
            continue
        if first_day and when < first_day:
            continue
        if last_day and when > last_day:
            continue
        out.append(entry)
    return sorted(out, key=lambda row: (row.reported, row.headline), reverse=True)


def from_briefing(item, *, document: str, reported: date | None, today: date | None = None,
                  chemicals: Sequence[str] | None = None,
                  geographies: Sequence[str] | None = None,
                  implication: str = "", attribution: str = "") -> Entry:
    """One parsed briefing item, as a row. What is passed in overrides what was parsed, because a
    person looked at it on the screen first."""
    today = today or date.today()
    where = tuple(geographies if geographies is not None else item.geographies)
    what = tuple(chemicals if chemicals is not None else item.chemicals)
    return Entry(
        reported=(item.reported or reported or today).isoformat(),
        event_date=item.day.isoformat() if item.day else "",
        precision=item.precision,
        headline=item.headline,
        kind=item.kind,
        chemicals=";".join(what),
        geographies=";".join(where),
        confidence=item.confidence,
        context=item.context,
        implication=implication or item.implication,
        quote=item.quote,
        attribution=attribution,
        document=document,
        stored_on=today.isoformat(),
    )
