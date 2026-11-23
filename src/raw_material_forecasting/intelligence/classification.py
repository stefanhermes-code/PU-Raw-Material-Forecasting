from __future__ import annotations
from urllib.parse import urlparse
from ..domain import SourceDecision


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _matches(host: str, domains: list[str]) -> bool:
    return any(host == d.lower().removeprefix("www.") or host.endswith("." + d.lower().removeprefix("www.")) for d in domains)


def classify_source(publisher_url: str, watchlist: dict) -> SourceDecision:
    host = _host(publisher_url)
    if _matches(host, watchlist.get("official_domains", [])):
        return SourceDecision("TIER_A", "OFFICIAL_PUBLIC")
    if _matches(host, watchlist.get("secondary_domains", [])):
        return SourceDecision("TIER_B", "REVIEW_REQUIRED")
    return SourceDecision("TIER_C", "UNKNOWN")


def classify_activation(*, source_tier: str, rights_clear: bool, extraction_confidence: float, identity_complete: bool, timing_complete: bool) -> str:
    if source_tier == "TIER_A" and rights_clear and extraction_confidence >= 0.95 and identity_complete and timing_complete:
        return "AUTO_APPROVED"
    return "EXTRACTED"
