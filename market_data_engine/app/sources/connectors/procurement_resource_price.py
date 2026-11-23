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


class ProcurementResourcePriceConnector:
    """Procurement Resource's free price-trend page: one table of country, Incoterm, price, month.

        Product | Region | Incoterm Basis | Price | Last Updated Month
        Polyether Polyol | Germany | CIF | USD 3,057.56/MT | July 2026

    Every row carries its own month, so the page's current and previous month are both read.
    Only the countries in ``countries`` are kept, mapped to the market they belong to
    (Germany -> Europe, with Germany kept as the destination).
    Verified against the live page 21 September 2026 (July and June 2026 rows).
    """

    allow_empty_parse = False
    _MARKERS = ("/MT",)

    def __init__(self, source_id: str, source_url: str, product: str, countries: dict[str, str]):
        self.source_id = source_id
        self.source_url = source_url
        self.product = product
        self.countries = countries

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
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        return re.sub(r"[\s\xa0]+", " ", text)

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        text = self._visible_text(html)
        out: dict[tuple[str, datetime], ExtractedRecord] = {}
        row = re.compile(
            rf"{re.escape(self.product)}\s+(?P<country>[A-Z][A-Za-z .]+?)\s+(?P<basis>EXW|FCA|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)"
            rf"\s+USD\s+(?P<price>[\d,]+(?:\.\d+)?)\s*/\s*MT\s+(?P<month>{_MONTH_NAMES})\s+(?P<year>20\d{{2}})", re.I)
        for m in row.finditer(text):
            country = m.group("country").strip()
            market = self.countries.get(country)
            if market is None:
                continue
            observed = datetime(int(m.group("year")), _MONTHS[m.group("month").capitalize()], 1, tzinfo=timezone.utc)
            key = (country, observed)
            if key in out:
                continue
            out[key] = ExtractedRecord(
                kind="price",
                source_url=source_url or self.source_url,
                publication_date=observed,
                data={
                    "type": "price",
                    "product": self.product,
                    "market_geography": market,
                    "destination": country,
                    "commercial_basis": m.group("basis").upper(),
                    "price": round(float(m.group("price").replace(",", "")), 2),
                    "currency": "USD",
                    "unit": "t",
                    "observation_type": price_kinds.ASSESSED,
                    "observation_date": observed,
                    "source_label": f"Procurement Resource {country} {m.group('basis').upper()} {self.product}, "
                                    f"{m.group('month').capitalize()} {m.group('year')}",
                },
            )
        return list(out.values())
