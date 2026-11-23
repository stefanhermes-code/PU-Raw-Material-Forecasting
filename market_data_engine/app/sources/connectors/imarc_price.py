from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds

_MONTHS = {name: i for i, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
_MONTH_NAMES = "|".join(_MONTHS)
_REGIONS = ("Northeast Asia", "Southeast Asia", "North America", "Middle East", "Europe")
_REGION_RE = "|".join(_REGIONS)


class ImarcPriceConnector:
    """IMARC's free pricing-report page: a monthly price per region in US$/kg.

    The page carries a heading "... Prices August 2026" over a table
        Region | Price (USD/Kg) | Latest Movement
        Europe | 2.79           | 10.3% Up
    and quarterly commentary in the form
        "The toluene diisocyanate (TDI) prices in Europe reached 2.54 USD/KG in March 2026."
    Both are read. IMARC states no delivery term and no grade for these figures, so the basis
    is REGIONAL_MARKET and the grade stays empty. MDI is recorded as polymeric MDI: in flexible
    foam MDI means pMDI (Stefan, 22 September 2026).
    Verified against the live page 21 September 2026 (page last updated 21 August 2026).
    """

    allow_empty_parse = False
    _MARKERS = ("USD/Kg", "USD/KG")

    def __init__(self, source_id: str, source_url: str, product: str, label: str):
        self.source_id = source_id
        self.source_url = source_url
        self.product = product
        self.label = label

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
        if payload and self.extract_html(payload.decode("utf-8", "replace"), item.source_url):
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
        html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
        html = re.sub(r"<meta\b[^>]*>", " ", html, flags=re.I)
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        return re.sub(r"[\s\xa0]+", " ", text)

    def _record(self, region: str, price_per_kg: str, observed: datetime, source_url: str | None,
                how: str) -> ExtractedRecord:
        return ExtractedRecord(
            kind="price",
            source_url=source_url or self.source_url,
            publication_date=observed,
            data={
                "type": "price",
                "product": self.product,
                "market_geography": region,
                "commercial_basis": "REGIONAL_MARKET",
                "price": round(float(price_per_kg) * 1000.0, 2),   # US$/kg -> US$/t
                "currency": "USD",
                "unit": "t",
                "observation_type": price_kinds.ASSESSED,
                "observation_date": observed,
                "source_label": f"IMARC {region} {self.label} price, {observed:%B %Y} ({how})",
            },
        )

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        text = self._visible_text(html)
        out: dict[tuple[str, datetime], ExtractedRecord] = {}

        # The monthly table, dated by the heading above it.
        heading = re.search(rf"Prices\s+(?:in\s+)?(?P<month>{_MONTH_NAMES})\s+(?P<year>20\d{{2}})", text)
        if heading:
            observed = datetime(int(heading.group("year")), _MONTHS[heading.group("month")], 1, tzinfo=timezone.utc)
            table = re.search(r"Region\s+Price\s*\(USD/Kg\)", text[heading.end():], re.I)
            if table:
                body = text[heading.end() + table.end():][:1500]
                for m in re.finditer(rf"(?P<region>{_REGION_RE})\s+(?P<price>\d+\.\d+)\s+-?\d+(?:\.\d+)?\s*%", body):
                    key = (m.group("region"), observed)
                    if key not in out:
                        out[key] = self._record(m.group("region"), m.group("price"), observed, source_url, "table")

        # Dated commentary: "... prices in Europe reached 2.54 USD/KG in March 2026."
        for m in re.finditer(
                rf"prices?\s+in\s+(?:the\s+)?(?P<region>{_REGION_RE})\s+reached\s+(?P<price>\d+\.\d+)\s*USD/KG"
                rf"\s+in\s+(?P<month>{_MONTH_NAMES})\s+(?P<year>20\d{{2}})", text, re.I):
            region = next(r for r in _REGIONS if r.lower() == m.group("region").lower())
            observed = datetime(int(m.group("year")), _MONTHS[m.group("month").capitalize()], 1, tzinfo=timezone.utc)
            key = (region, observed)
            if key not in out:
                out[key] = self._record(region, m.group("price"), observed, source_url, "commentary")
        return list(out.values())
