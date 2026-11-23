from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds


class BusinessAnalytiqTDIConnector:
    """Business Analytiq public monthly TDI price index, five regions, free of charge.

    The page states, in plain text, a monthly price per region in US$/KG, e.g.
        "Toluene diisocyanate price August 2026 and outlook"
        "North America:US$2.28/KG, 14% up"
        "Europe:US$2.71/KG, 12.9% up"
    Business Analytiq does not state an Incoterm - the figures are regional market
    assessments compiled from several underlying sources - so the commercial basis is
    recorded as REGIONAL_MARKET rather than inventing a CFR/FOB that the publisher
    never claimed. Governance decision (Stefan, 2026-09-08): these are stored as full
    price observations. Verified against the live page 2026-09-08.
    """

    source_id = "businessanalytiq_tdi"
    source_url = "https://businessanalytiq.com/procurementanalytics/index/toluene-diisocyanate-price-index/"
    product = "TDI"
    allow_empty_parse = False

    # Every chemical Business Analytiq indexes uses this page. The connector took one URL and one
    # product name as class attributes until 1 October 2026, when Stefan asked for the European
    # feedstocks: benzene, toluene and propylene oxide are the same page with a different heading,
    # so the connector takes them as arguments and the TDI registration goes on working untouched.
    def __init__(self, source_id: str | None = None, source_url: str | None = None,
                 product: str | None = None):
        if source_id:
            self.source_id = source_id
        if source_url:
            self.source_url = source_url
        if product:
            self.product = product

    # Seven on the feedstock pages; the isocyanate pages print five. Listing all seven costs
    # nothing on a page that carries fewer.
    _REGIONS = ("North America", "Europe", "Northeast Asia", "Southeast Asia", "Middle East",
                "South America", "India")
    _MONTHS = {name: i for i, name in enumerate(
        "January February March April May June July August September October November December".split(), 1)}
    _ROW_RE = re.compile(
        r"(?P<region>North America|Europe|Northeast Asia|Southeast Asia|Middle East|South America|India)"
        r"\s*:\s*US\$\s*(?P<price>[0-9]+(?:\.[0-9]+)?)\s*/\s*KG", re.I)
    # "Toluene diisocyanate price August 2026", and on the feedstock pages the heading carries the
    # word index or trend between the two: "Benzene price index September 2026".
    _MONTH_RE = re.compile(
        r"price\s*(?:index|trend)?\s+"
        r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(?P<year>20\d{2})", re.I)
    _MARKERS = ("US$", "/KG")

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _fetch_http(self, item: SourceItem) -> bytes:
        response = httpx.get(item.source_url, follow_redirects=True, timeout=30.0, headers=self._headers())
        response.raise_for_status()
        return response.content

    def fetch_item(self, item: SourceItem) -> bytes:
        """Plain HTTP first; a real off-screen Edge only if the page withheld the figures."""
        try:
            payload = self._fetch_http(item)
        except Exception:
            payload = b""
        if payload and self._ROW_RE.search(self._visible_text(payload.decode("utf-8", "replace"))):
            return payload
        from app.sources.edge_cdp import EdgeUnavailable, fetch_rendered_text

        try:
            text = fetch_rendered_text(item.source_url, any_markers=self._MARKERS)
        except EdgeUnavailable:
            if payload:
                return payload
            raise
        return (
            '<html><body data-acquisition="edge-cdp"><pre>' + html_lib.escape(text) + "</pre></body></html>"
        ).encode("utf-8")

    @staticmethod
    def _visible_text(html: str) -> str:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        return re.sub(r"[ \t\xa0]+", " ", text)

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        text = self._visible_text(html)
        month = self._MONTH_RE.search(text)
        if not month:
            return []
        observed = datetime(int(month.group("year")), self._MONTHS[month.group("month").capitalize()], 1,
                            tzinfo=timezone.utc)
        out: list[ExtractedRecord] = []
        seen: set[str] = set()
        for match in self._ROW_RE.finditer(text):
            region = match.group("region").title().replace("Northeast", "Northeast")
            if region in seen:
                continue
            seen.add(region)
            out.append(ExtractedRecord(
                kind="price",
                source_url=source_url or self.source_url,
                publication_date=observed,
                data={
                    "type": "price",
                    "product": self.product,
                    "market_geography": region,
                    "commercial_basis": "REGIONAL_MARKET",
                    "price": round(float(match.group("price")) * 1000.0, 2),   # US$/KG -> US$/t
                    "currency": "USD",
                    "unit": "t",
                    "observation_type": price_kinds.ASSESSED,
                    "observation_date": observed,
                    "source_label": f"Business Analytiq {region} TDI price index, {month.group('month')} {month.group('year')}",
                },
            ))
        return out


# The name the registry has used since September 2026. The class is no longer TDI-only.
BusinessAnalytiqPriceConnector = BusinessAnalytiqTDIConnector
