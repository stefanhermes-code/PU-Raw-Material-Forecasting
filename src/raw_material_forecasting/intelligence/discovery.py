from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import re
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests

from ..domain import CandidateStateRecord, DiscoveryCandidate
from ..repositories import MarketRepository

USER_AGENT = "HTCGlobal-PURawMaterialForecasting/0.4 (+market-intelligence-discovery)"


@dataclass(frozen=True)
class ScanResult:
    discovered: int
    inserted: int
    skipped: int
    errors: tuple[str, ...]


def google_news_rss_url(query: str, days: int = 7) -> str:
    return "https://news.google.com/rss/search?q=" + quote_plus(f"{query} when:{days}d") + "&hl=en&gl=US&ceid=US:en"


def _publisher_link(description: str) -> str:
    text = html.unescape(description or "")
    links = re.findall(r'href=["\'](https?://[^"\']+)["\']', text, flags=re.I)
    for link in links:
        if "news.google." not in link:
            return link
    return "UNKNOWN"


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_google_news_rss(xml_bytes: bytes, *, query_key: str, discovered_at: datetime) -> list[DiscoveryCandidate]:
    root = ET.fromstring(xml_bytes)
    out: list[DiscoveryCandidate] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        google_url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description = item.findtext("description") or ""
        source_el = item.find("source")
        publisher = (source_el.text or "").strip() if source_el is not None else "UNKNOWN"
        home = (source_el.attrib.get("url") or "UNKNOWN").strip() if source_el is not None else "UNKNOWN"
        pub_url = _publisher_link(description)
        key_material = guid or f"{title}|{pub_url}|{google_url}"
        cid = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        summary = re.sub(r"<[^>]+>", " ", html.unescape(description))
        summary = re.sub(r"\s+", " ", summary).strip()[:1000]
        out.append(DiscoveryCandidate(
            candidate_id=cid, title=title, google_news_url=google_url, publisher=publisher,
            publisher_url=pub_url, publisher_home_url=home, published_at=_parse_date(item.findtext("pubDate") or ""),
            discovered_at=discovered_at, query_key=query_key, raw_summary=summary,
        ))
    return out


def store_discovery_candidates(repo: MarketRepository, candidates: list[DiscoveryCandidate]) -> tuple[int, int]:
    inserted = skipped = 0
    for c in candidates:
        if repo.insert_discovery_candidate(c):
            inserted += 1
            repo.record_candidate_state(CandidateStateRecord(c.candidate_id, "DISCOVERED", c.discovered_at, "system:discovery", f"discovered via {c.query_key}"))
        else:
            skipped += 1
    return inserted, skipped


def run_watchlist_scan(repo: MarketRepository, watchlist: dict, *, days: int = 7, now: datetime | None = None, session=requests) -> ScanResult:
    now = now or datetime.now(timezone.utc)
    discovered = inserted = skipped = 0
    errors: list[str] = []
    for query in watchlist.get("queries", []):
        try:
            resp = session.get(google_news_rss_url(query, days), timeout=20, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            candidates = parse_google_news_rss(resp.content, query_key=query, discovered_at=now)
            discovered += len(candidates)
            ins, sk = store_discovery_candidates(repo, candidates)
            inserted += ins; skipped += sk
        except Exception as exc:
            errors.append(f"{query}: {exc}")
    return ScanResult(discovered, inserted, skipped, tuple(errors))
