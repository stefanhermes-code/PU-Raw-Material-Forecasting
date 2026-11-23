"""The weekly Global Polyurethane Industry Briefing, read into rows the application can hold.

HTC Global writes this briefing every week out of the PU Observatory and the PI3 engines, which is
why its text can be used in a customer's report without naming anyone: it is our own writing. What
it carries that a price series cannot is the reason a price moved - a producer's increase, a plant
shutdown, an incident, an export shift.

The briefing follows one shape, week after week: a numbered item whose heading is already a
complete statement, then Status, then Scope, then the body, then why it matters and what to do
about it. So this reads it with rules rather than with a model. Nothing here infers, summarises or
rewrites: every field is lifted from the document, and the date a row carries is read out of a
sentence that is kept beside it. A row whose date cannot be found in the text keeps the briefing's
own period instead of being given one.

Two things in a briefing never travel. The numbered references name third parties, so they stay in
the attribution and out of anything a customer reads. And the prices quoted inside an item are
other people's assessments of series we already collect ourselves - reading them in here would
import someone else's number through the back door and count our own market twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
# What the Status line is worth. The weakest reading in the line wins: "Wanhua shutdown confirmed;
# Tosoh timing remains a market indication" is an indication, because half of it is.
CONFIRMED, INDICATION, UNCONFIRMED, UNKNOWN = "CONFIRMED", "INDICATION", "UNCONFIRMED", "UNKNOWN"
_WEAK = ("unconfirmed", "not independently", "no transaction announced", "no confirming")
_MIDDLE = ("indication", "reported by", "reported through", "market report", "not transaction",
           "partly based", "based on sources", "from sources")
_STRONG = ("confirmed", "official", "peer-reviewed", "regulatory disclosure", "announcement")

# Which chemical a scope line is talking about, in our own codes. Bare MDI covers both the polymeric
# and the pure market: a shutdown at Yantai or a producer's increase does not stop at one of them.
CHEMICAL_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("polymeric mdi", ("PMDI",)), ("pmdi", ("PMDI",)),
    ("pure mdi", ("PURE_MDI",)), ("monomeric mdi", ("PURE_MDI",)), ("mmdi", ("PURE_MDI",)),
    ("mdi", ("PMDI", "PURE_MDI")),
    ("tdi", ("TDI",)),
    ("flexible-foam polyether", ("POLYETHER_POLYOL",)), ("polyether", ("POLYETHER_POLYOL",)),
    ("pop", ("GRAFT_POLYOL",)), ("graft", ("GRAFT_POLYOL",)),
    ("propylene oxide", ("PO",)), ("po/eo", ("PO",)),
    ("polyester polyol", ("AROMATIC_POLYESTER_POLYOL", "ALIPHATIC_POLYESTER_POLYOL")),
    ("benzene", ("BENZENE",)), ("toluene", ("TOLUENE",)), ("aniline", ("ANILINE",)),
)
# The regions a scope line names, mapped onto the geographies the application prices.
#
# Two of these are buckets rather than markets. A briefing that says Global is about every market we
# price, and one that says Asia is about all of them in Asia; neither may be quietly narrowed to the
# one region that happens to match a word, because geography must never narrow market intelligence.
# Longest name first, and ground already matched is not matched again, so "South Asia" is South Asia
# and not Asia, and "ASEAN and South Asia" never becomes global.
WORLDWIDE = "Worldwide"
ASIA = "Asia"
IN_ASIA = ("China", "Northeast Asia", "Southeast Asia", "South Asia", "India")
GEOGRAPHY_WORDS: tuple[tuple[str, str], ...] = (
    ("northeast asia", "Northeast Asia"), ("southeast asia", "Southeast Asia"),
    ("south asia", "South Asia"), ("asean", "Southeast Asia"), ("india", "India"),
    ("china", "China"), ("japan", "Northeast Asia"), ("south korea", "Northeast Asia"),
    ("taiwan", "Northeast Asia"),
    ("north america", "North America"), ("south america", "South America"),
    ("emeai", "Europe"), ("europe", "Europe"), ("middle east", "Middle East"),
    ("worldwide", WORLDWIDE), ("global", WORLDWIDE), ("asia", ASIA),
)
# A first suggestion at what kind of event this is. It is a suggestion: the person storing the row
# sees it in a list and changes it when it is wrong.
TYPE_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("price increase", "raises", "increase across", "price action", "surcharge"), "Price action"),
    (("maintenance", "shutdown", "restart", "outage", "incident", "force majeure"), "Operations"),
    (("anti-dumping", "regulation", "sunset review", "duty", "consultation"), "Regulation"),
    (("takeover", "acquisition", "stake", "exit", "sale", "merger"), "Corporate"),
    (("export", "import", "customs", "balancing source", "trade"), "Trade"),
    (("capacity", "new plant", "complex", "expansion", "feasibility", "start-up"), "New venture"),
    (("launch", "introduces", "commercialis", "commercializ", "catalyst", "product"), "Product"),
)

_ITEM = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_DAY = re.compile(
    r"\b(?:on|dated|from|since|of|effective|beginning|starting)\s+(\d{1,2})\s+"
    r"([A-Za-z]+)(?:\s+(\d{4}))?\b", re.I)
# "7-13 September 2026" and "28 September-4 October 2026" are both written, and the second one has
# a month on each side of the dash.
_PERIOD = re.compile(
    r"identified:\s*(\d{1,2})\s*(?:([A-Za-z]+)\s*)?(?:-|to)\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
    re.I)
_PREPARED = re.compile(r"^Prepared:?\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I | re.M)
_REFERENCE = re.compile(r"\[\d+\]")
# How hard HTC Global intends to chase the item. It opens most Practical implication lines and it
# is our own triage, which has no place in a document a customer reads.
_TRIAGE = re.compile(r"^(?:high|medium-high|medium|low)\s+follow[- ]up[^.]*\.\s*", re.I)


@dataclass
class BriefingItem:
    """One numbered item, as the briefing wrote it."""

    number: int
    headline: str
    status_line: str = ""
    confidence: str = UNKNOWN
    scope_line: str = ""
    chemicals: tuple[str, ...] = ()
    geographies: tuple[str, ...] = ()
    kind: str = ""
    body: str = ""
    context: str = ""        # why it matters
    implication: str = ""    # what to do about it, as the briefing put it
    day: date | None = None
    precision: str = "PERIOD"
    # When the briefing carried it. A September report is about what moved the market in September,
    # and an August shutdown written up in the second week of September moved it in September.
    reported: date | None = None
    quote: str = ""          # the sentence the date was read out of

    @property
    def prints_for_a_customer(self) -> bool:
        """Stefan, 8 October 2026: confirmed plainly, an indication as an indication, nothing else."""
        return self.confidence in (CONFIRMED, INDICATION)


@dataclass
class Briefing:
    """One week's briefing."""

    name: str = ""
    prepared: date | None = None
    covers_from: date | None = None
    covers_to: date | None = None
    items: list[BriefingItem] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def looks_like_a_briefing(text: str) -> bool:
    head = (text or "")[:2000].lower()
    return "polyurethane" in head and "briefing" in head and "consequential developments" in head


def _month(word: str) -> int | None:
    return MONTHS.get(str(word or "").strip().lower().rstrip(","))


def confidence_of(status_line: str) -> str:
    """How far a reader may lean on this item, taken from the briefing's own Status line."""
    text = str(status_line or "").lower()
    if not text.strip():
        return UNKNOWN
    if any(word in text for word in _WEAK):
        return UNCONFIRMED
    if any(word in text for word in _MIDDLE):
        return INDICATION
    if any(word in text for word in _STRONG):
        return CONFIRMED
    return UNKNOWN


def chemicals_in(scope_line: str) -> tuple[str, ...]:
    text = str(scope_line or "").lower()
    found: list[tuple[int, str]] = []
    matched_spans: list[tuple[int, int]] = []
    for word, codes in CHEMICAL_WORDS:
        start = text.find(word)
        while start != -1:
            # A longer name already covered this ground: "polymeric MDI" must not also count as MDI.
            if not any(low <= start < high for low, high in matched_spans):
                matched_spans.append((start, start + len(word)))
                for offset, code in enumerate(codes):
                    if code not in [seen for _at, seen in found]:
                        found.append((start + offset, code))
            start = text.find(word, start + 1)
    return tuple(code for _at, code in sorted(found))


def geographies_in(scope_line: str) -> tuple[str, ...]:
    text = str(scope_line or "").lower()
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for word, name in GEOGRAPHY_WORDS:
        start = text.find(word)
        while start != -1:
            if not any(low <= start < high for low, high in taken):
                taken.append((start, start + len(word)))
                if name not in [seen for _at, seen in found]:
                    found.append((start, name))
            start = text.find(word, start + 1)
    # In the order the scope line names them, which is the order a reader of that line would say.
    names = tuple(name for _at, name in sorted(found))
    # Worldwide swallows the rest: an item that says Global is not about one region.
    return (WORLDWIDE,) if WORLDWIDE in names else names


def kind_of(headline: str, body: str = "") -> str:
    text = f"{headline} {body}".lower()
    for words, name in TYPE_WORDS:
        if any(word in text for word in words):
            return name
    return ""


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.;])\s+", str(text or "")) if part.strip()]


def _dated_sentence(body: str, year: int, prepared: date | None) -> tuple[date | None, str]:
    """The first dated sentence in the item, and the day it names. Nothing is inferred."""
    for sentence in _sentences(body):
        match = _DAY.search(sentence)
        if not match:
            continue
        month = _month(match.group(2))
        if month is None:
            continue
        said_year = int(match.group(3)) if match.group(3) else year
        # A briefing prepared in January reporting "on 28 December" means the December just gone.
        if not match.group(3) and prepared is not None and month - prepared.month > 2:
            said_year -= 1
        try:
            return date(said_year, month, int(match.group(1))), _REFERENCE.sub("", sentence).strip()
        except ValueError:
            continue
    return None, ""


def read(text: str, *, name: str = "") -> Briefing:
    """A briefing's text, as `document_intake` hands it over, read into its items."""
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    out = Briefing(name=name)
    joined = "\n".join(lines)

    prepared = _PREPARED.search(joined)
    if prepared and _month(prepared.group(2)):
        out.prepared = date(int(prepared.group(3)), _month(prepared.group(2)),
                            int(prepared.group(1)))
    covers = _PERIOD.search(joined)
    if covers and _month(covers.group(4)):
        last_month, year = _month(covers.group(4)), int(covers.group(5))
        first_month = _month(covers.group(2)) or last_month
        first_year = year - 1 if first_month > last_month else year
        out.covers_from = date(first_year, first_month, int(covers.group(1)))
        out.covers_to = date(year, last_month, int(covers.group(3)))
    year = (out.prepared or out.covers_to or date.today()).year

    current: BriefingItem | None = None
    field_now = "body"
    body: list[str] = []
    in_sources = False

    def close(item: BriefingItem | None, parts: list[str]) -> None:
        if item is None:
            return
        item.body = "\n".join(parts).strip()
        item.chemicals = chemicals_in(item.scope_line)
        item.geographies = geographies_in(item.scope_line)
        item.confidence = confidence_of(item.status_line)
        item.kind = kind_of(item.headline) or kind_of("", item.body)
        item.reported = out.covers_to or out.prepared
        day, quote = _dated_sentence(item.body, year, out.prepared)
        if day is not None:
            item.day, item.precision, item.quote = day, "DAY", quote
        elif out.covers_to is not None:
            # No date in the text. The briefing's own week is what we know, and the row says so
            # rather than being given a day nobody wrote down.
            item.day, item.precision = out.covers_to, "PERIOD"
            item.quote = f"Consequential developments identified: week ending {out.covers_to}"
        out.items.append(item)

    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("sources") and len(stripped) < 12:
            close(current, body)
            current, body, in_sources = None, [], True
            continue
        if in_sources:
            if stripped:
                out.sources.append(_REFERENCE.sub("", stripped).strip())
            continue
        match = _ITEM.match(stripped)
        if match and not stripped.endswith(":"):
            close(current, body)
            current = BriefingItem(number=int(match.group(1)),
                                   headline=_REFERENCE.sub("", match.group(2)).strip())
            body, field_now = [], "body"
            continue
        if current is None:
            continue
        if low.startswith("status:"):
            current.status_line = stripped.split(":", 1)[1].strip()
            continue
        if low.startswith("scope:"):
            current.scope_line = stripped.split(":", 1)[1].strip()
            continue
        if low.startswith("why it matters"):
            field_now = "context"
            current.context = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            continue
        if low.startswith("practical implication"):
            field_now = "implication"
            said = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            current.implication = _TRIAGE.sub("", said).lstrip()
            continue
        if not stripped:
            continue
        clean = _REFERENCE.sub("", stripped).strip()
        if field_now == "body":
            body.append(clean)
        elif field_now == "context":
            current.context = (current.context + " " + clean).strip()
        else:
            current.implication = (current.implication + " " + clean).strip()
            current.implication = _TRIAGE.sub("", current.implication).lstrip()
    close(current, body)

    if not out.items:
        out.notes.append("No numbered items were found in this document.")
    unplaced = [item.number for item in out.items if not item.chemicals]
    if unplaced:
        out.notes.append(
            "These items name no chemical we price and need one chosen by hand: "
            + ", ".join(str(number) for number in unplaced))
    return out
