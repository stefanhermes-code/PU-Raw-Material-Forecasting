from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Iterable

CUSTOMER_VISIBLE_CATEGORIES = frozenset({
    "CAPACITY_ADDITION",
    "CAPACITY_EXPANSION",
    "DEBOTTLENECK",
    "PLANT_STARTUP",
    "PERMANENT_CLOSURE",
    "STRUCTURAL_REDUCTION",
    "CURTAILMENT",
    "PLANNED_MAINTENANCE",
    "SHUTDOWN",
    "FORCE_MAJEURE",
    "INCIDENT",
    "PRICE_INCREASE",
    "PRICE_DECREASE",
    "PRICE_ACTION",
    # Capacity and corporate direction. A company that buys TDI asks who is building, who is putting
    # money in and who is leaving, and none of those questions were answerable here: the engine had
    # no rules to produce these categories and this set had no entries to let them through.
    "CAPACITY_INVESTMENT",
    "CORPORATE_VENTURE",
    "CORPORATE_EXIT",
})

DEFAULT_FEED_PATH = Path("data/producer_signals.json")


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class ProducerSignal:
    signal_id: str
    producer: str
    chemical: str
    geography: str
    category: str
    what_changed: str
    announced_at: date
    effective_from: date | None
    effective_to: date | None
    status: str
    market_relevance: str
    verification_status: str
    source_url: str
    source_date: date | None
    # How well the report is evidenced (which publishers carried it). Internal only: a customer
    # cannot act on our sourcing, and naming our publishers on their screen gives away where the
    # intelligence comes from. Defaulted so older feeds still load.
    evidence_note: str = ""

    @classmethod
    def from_mapping(cls, row: dict) -> "ProducerSignal":
        announced = _parse_date(row.get("announced_at"))
        if announced is None:
            raise ValueError("Producer signal requires announced_at")
        return cls(
            signal_id=str(row.get("signal_id") or "").strip(),
            producer=str(row.get("producer") or "").strip(),
            chemical=str(row.get("chemical") or "").strip(),
            geography=str(row.get("geography") or "").strip(),
            category=str(row.get("category") or "").strip().upper(),
            what_changed=str(row.get("what_changed") or "").strip(),
            announced_at=announced,
            effective_from=_parse_date(row.get("effective_from")),
            effective_to=_parse_date(row.get("effective_to")),
            status=str(row.get("status") or "").strip().upper(),
            market_relevance=str(row.get("market_relevance") or "").strip(),
            verification_status=str(row.get("verification_status") or "").strip().upper(),
            source_url=str(row.get("source_url") or "").strip(),
            source_date=_parse_date(row.get("source_date")),
            evidence_note=str(row.get("evidence_note") or "").strip(),
        )


def load_producer_signals(path: Path = DEFAULT_FEED_PATH) -> list[ProducerSignal]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Producer signal feed must be a JSON array")
    return [ProducerSignal.from_mapping(dict(row)) for row in payload]


def customer_visible_signals(
    signals: Iterable[ProducerSignal],
    *,
    chemicals: Iterable[str] | None = None,
    geographies: Iterable[str] | None = None,
) -> list[ProducerSignal]:
    """What the reader is shown.

    GEOGRAPHY DOES NOT NARROW MARKET INTELLIGENCE. A joint venture abandoned in Saudi Arabia and a
    new propylene unit in Zhejiang both bear on what a company buying TDI in China will face; hiding
    them because the signal did not say "China" would leave the reader with a view of their market
    that stops at the border, which is not how the market works. Geography is reported as a column
    and the reader filters on it if they want to. The parameter is kept so existing callers still
    work, and is deliberately ignored.

    A signal that carries a chain relationship is kept whatever its chemical, for the same reason:
    the relationship sentence exists to explain why an off-product signal matters, and filtering the
    signal away means nobody ever reads it.

    A signal that names NO chemical is also kept. Not knowing which product a venture or a withdrawal
    touches is not the same as knowing it is irrelevant, and a producer of TDI restructuring itself is
    market intelligence whether or not the headline happened to say TDI.

    So the chemical scope narrows exactly one case: a signal that names a product outside the scope
    and carries no chain relationship to anything inside it.
    """
    chemical_filter = {str(item).strip().lower() for item in chemicals or []}
    visible = []
    for signal in signals:
        if signal.verification_status != "VERIFIED":
            continue
        if signal.category not in CUSTOMER_VISIBLE_CATEGORIES:
            continue
        if (chemical_filter and "*" not in chemical_filter
                and signal.chemical
                and not signal.market_relevance
                and signal.chemical.lower() not in chemical_filter):
            continue
        visible.append(signal)
    return sorted(visible, key=lambda item: (item.announced_at, item.signal_id), reverse=True)


PRICING_CATEGORIES = frozenset({"PRICE_INCREASE", "PRICE_DECREASE", "PRICE_ACTION"})

CORPORATE_CATEGORIES = frozenset({"CORPORATE_VENTURE", "CORPORATE_EXIT"})

PRICING_KIND = "Pricing action"
CAPACITY_KIND = "Capacity / availability"
# Who owns and runs the capacity, which is a different question from how much of it there is.
CORPORATE_KIND = "Ownership / venture"


def signal_kind(signal: ProducerSignal) -> str:
    if signal.category in PRICING_CATEGORIES:
        return PRICING_KIND
    if signal.category in CORPORATE_CATEGORIES:
        return CORPORATE_KIND
    return CAPACITY_KIND


def filter_options(signals: Iterable[ProducerSignal]) -> dict[str, list[str]]:
    """The values actually present, so a filter never offers a choice that returns nothing."""
    signals = list(signals)
    return {
        "producers": sorted({s.producer for s in signals if s.producer}),
        "chemicals": sorted({s.chemical for s in signals if s.chemical}),
        "geographies": sorted({s.geography for s in signals if s.geography}),
        "kinds": sorted({signal_kind(s) for s in signals}),
    }


def filter_signals(
    signals: Iterable[ProducerSignal],
    *,
    since: date | None = None,
    until: date | None = None,
    producers: Iterable[str] | None = None,
    chemicals: Iterable[str] | None = None,
    geographies: Iterable[str] | None = None,
    kinds: Iterable[str] | None = None,
) -> list[ProducerSignal]:
    """Narrow the signal set. An empty or omitted filter means "everything", never "nothing".

    Signals are not aged out of the feed - a capacity addition from last year is still a fact about
    the market. But a count over everything ever collected says little: two months from now the
    headline number will keep climbing while telling the reader nothing about what is recent. The
    timeframe is therefore a lens the reader chooses, not a rule the engine imposes.
    """
    producer_set = {str(v).strip() for v in producers or [] if str(v).strip()}
    chemical_set = {str(v).strip() for v in chemicals or [] if str(v).strip()}
    geography_set = {str(v).strip() for v in geographies or [] if str(v).strip()}
    kind_set = {str(v).strip() for v in kinds or [] if str(v).strip()}

    out = []
    for signal in signals:
        if since and signal.announced_at < since:
            continue
        if until and signal.announced_at > until:
            continue
        if producer_set and signal.producer not in producer_set:
            continue
        if chemical_set and signal.chemical not in chemical_set:
            continue
        if geography_set and signal.geography not in geography_set:
            continue
        if kind_set and signal_kind(signal) not in kind_set:
            continue
        out.append(signal)
    return out


def _effective_status(signal: ProducerSignal) -> str:
    if signal.effective_from and signal.effective_to:
        return f"{signal.effective_from.isoformat()} to {signal.effective_to.isoformat()} · {signal.status}"
    if signal.effective_from:
        return f"From {signal.effective_from.isoformat()} · {signal.status}"
    return signal.status or "ANNOUNCED"


def customer_display_rows(signals: Iterable[ProducerSignal]) -> list[dict[str, str]]:
    """Customer-facing rows.

    "Announced" leads: a market signal without its date is not intelligence, it is a rumour - the
    reader cannot tell a shutdown reported this morning from one reported in May.

    "Effective / status" is only rendered when at least one signal actually carries an effective date
    or a status other than ANNOUNCED. Otherwise every cell reads "ANNOUNCED" and the column is pure
    noise next to the date that now sits on the left.
    """
    signals = list(signals)
    show_status = any(
        signal.effective_from or signal.effective_to or (signal.status and signal.status != "ANNOUNCED")
        for signal in signals
    )
    rows: list[dict[str, str]] = []
    for signal in signals:
        row = {
            "Announced": signal.announced_at.isoformat(),
            "Producer": signal.producer,
            "Chemical": signal.chemical,
            # A publisher that named no geography is shown as such. Printing a market the publisher
            # never claimed is worse than an empty cell, and worse still than saying so plainly.
            "Geography": signal.geography or "Not stated",
            "What changed": signal.what_changed,
        }
        if show_status:
            row["Effective / status"] = _effective_status(signal)
        row["Market relevance"] = signal.market_relevance
        rows.append(row)
    return rows


def _varies(values: list[str]) -> bool:
    """True when a column would actually tell the reader something."""
    return len({value for value in values if value}) > 1


def internal_display_rows(signals: Iterable[ProducerSignal]) -> list[dict[str, object]]:
    """The internal review table.

    A column earns its place by varying. The previous version showed Signal ID, Category, Geography,
    Effective from, Effective to, Status and Verification alongside everything else - and of those,
    Effective from/to were never populated, Status read ANNOUNCED on every row, Verification read
    VERIFIED on every row (rejected signals never reach the feed), and Geography read China on every
    row. Seven columns of nothing pushed the ones that mattered off the right-hand edge, and a table
    you have to scroll sideways to read does not get read.

    So: date, who, what, how well evidenced, where to check. Category is dropped because the sentence
    in "What changed" already says it. Signal ID is dropped from the view and stays in the feed, where
    anything that needs to quote one can find it. Geography, effective dates and status return
    automatically the moment they carry more than one value.
    """
    signals = list(signals)
    show_geography = _varies([signal.geography for signal in signals])
    show_effective = any(signal.effective_from or signal.effective_to for signal in signals)
    show_status = _varies([signal.status for signal in signals])
    show_verification = _varies([signal.verification_status for signal in signals])

    rows: list[dict[str, object]] = []
    for signal in signals:
        row: dict[str, object] = {
            "Announced": signal.announced_at,
            "Producer": signal.producer,
            "Chemical": signal.chemical,
            "What changed": signal.what_changed,
        }
        if show_geography:
            row["Geography"] = signal.geography
        if show_effective:
            row["Effective"] = _effective_status(signal)
        if show_status:
            row["Status"] = signal.status
        row["Relevance"] = signal.market_relevance
        row["Evidence"] = signal.evidence_note
        if show_verification:
            row["Verification"] = signal.verification_status
        row["Source"] = signal.source_url
        rows.append(row)
    return rows


INTERNAL_LINK_COLUMNS = ("Source",)
