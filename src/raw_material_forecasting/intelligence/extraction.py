from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Protocol


@dataclass(frozen=True)
class ExtractedPriceCandidate:
    product_family: str; grade: str; origin: str; destination: str; price_basis: str; packaging: str; shipment_size: str
    value: float; currency: str; unit: str; observation_at: datetime; publication_at: datetime; source_key: str; identity_complete: bool


@dataclass(frozen=True)
class ExtractedEventCandidate:
    product_family: str; geography: str; event_type: str; publication_at: datetime; start_at: datetime | None; expected_end_at: datetime | None
    direction: str; severity: str; confidence: float; source_key: str


@dataclass(frozen=True)
class ExtractionResult:
    prices: tuple[ExtractedPriceCandidate, ...]
    events: tuple[ExtractedEventCandidate, ...]


class IntelligenceExtractor(Protocol):
    def extract(self, text: str, *, publication_at: datetime, source_key: str) -> ExtractionResult: ...


_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE = rf"{_MONTH}\s+\d{{1,2}},\s+\d{{4}}"


def _date(s: str) -> datetime:
    return datetime.strptime(s, "%B %d, %Y").replace(tzinfo=timezone.utc) if len(s.split()[0]) > 3 else datetime.strptime(s, "%b %d, %Y").replace(tzinfo=timezone.utc)


class ConservativePmdiExtractor:
    def extract(self, text: str, *, publication_at: datetime, source_key: str) -> ExtractionResult:
        lower = text.lower()
        product = "MDI" if "mdi" in lower else "UNKNOWN"
        grade = "polymeric MDI" if ("polymeric mdi" in lower or "pmdi" in lower) else "UNKNOWN"
        geography = "China" if "china" in lower else "UNKNOWN"
        prices: list[ExtractedPriceCandidate] = []
        pattern = re.compile(rf"(?P<value>\d{{3,6}}(?:\.\d+)?)\s*(?P<currency>RMB|CNY|USD|EUR)\s*/?\s*(?:metric\s*)?(?:tonne|ton|t)\b.*?\bon\s+(?P<date>{_DATE})", re.I)
        for m in pattern.finditer(text):
            currency = m.group("currency").upper().replace("CNY", "RMB")
            obs_at = _date(m.group("date"))
            basis = "China domestic benchmark" if "benchmark" in lower and geography == "China" else "UNKNOWN"
            identity_complete = all(x != "UNKNOWN" for x in (grade, geography, basis, "UNKNOWN", "UNKNOWN", currency))
            prices.append(ExtractedPriceCandidate(product, grade, geography, geography, basis, "UNKNOWN", "UNKNOWN", float(m.group("value")), currency, f"{currency}/MT", obs_at, publication_at, source_key, identity_complete))

        events: list[ExtractedEventCandidate] = []
        maint = re.search(rf"planned\s+maintenance.*?from\s+(?P<start>{_DATE})\s+(?:until|to|through)\s+(?P<end>{_DATE})", text, re.I | re.S)
        if maint and product != "UNKNOWN":
            events.append(ExtractedEventCandidate(product, geography, "planned_maintenance", publication_at, _date(maint.group("start")), _date(maint.group("end")), "SUPPLY_NEGATIVE", "MEDIUM", 0.90, source_key))
        return ExtractionResult(tuple(prices), tuple(events))
