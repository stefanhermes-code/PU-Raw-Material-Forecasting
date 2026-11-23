from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds


class EchemiProductConnector:
    """Configured ECHEMI visible-price connector. Does not bypass subscription controls."""

    allow_empty_parse = True
    _DATE_RE = r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}"

    # The page's own labelled figure: "<product> China Domestic Price Sep 11, 2026 12583.333Yuan/mt".
    #
    # An ECHEMI product page states its price twice and the two do not agree. Above the table sits a
    # summary sentence - "prices across 4 categories as of Sep 11, 2026: China Domestic at 10516.667
    # Yuan/mt, International from 1375 ..." - and that sentence lags. Its 11 September figure,
    # 10516.667, is the table's 8 September figure to three decimal places; the table on the 11th
    # says 12583.333.
    #
    # The old parser took the first date-and-price anywhere in the flattened page, which is always
    # that sentence. So polyol was stored 20% below Longzhong's print for the same day, polymeric
    # MDI 4% below, and toluene 12% below. Read against the table instead, ECHEMI and Longzhong agree
    # to within 0.9% on every day they both printed.
    _LABELLED = re.compile(
        rf"China\s+Domestic\s+Price\s*(?P<date>{_DATE_RE})\s*"
        r"(?P<price>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<unit>Yuan/(?:mt|ton))",
        re.I,
    )

    def __init__(self, *, source_id: str, source_url: str, product: str, grade: str | None, geography: str, basis: str):
        self.source_id = source_id
        self.source_url = source_url
        self.product = product
        self.grade = grade
        self.geography = geography
        self.basis = basis

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    _PRICE_MARKERS = ("Yuan/mt", "Yuan/ton", "USD/ton", "USD/mt")

    def _fetch_http(self, item: SourceItem) -> bytes:
        r = httpx.get(item.source_url, follow_redirects=True, timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.content

    def _fetch_rendered_edge(self, item: SourceItem) -> bytes:
        from app.sources.edge_cdp import fetch_rendered_text

        text = fetch_rendered_text(item.source_url, any_markers=self._PRICE_MARKERS)
        return (
            '<html><body data-acquisition="edge-cdp"><pre>' + html_lib.escape(text) + "</pre></body></html>"
        ).encode("utf-8")

    def fetch_item(self, item: SourceItem) -> bytes:
        # Same Aliyun WAF as the TDI curve pages: a real off-screen Edge read over CDP is
        # the primary path; plain HTTP only where Edge is unavailable (see edge_cdp.py).
        from app.sources.edge_cdp import EdgeUnavailable

        try:
            return self._fetch_rendered_edge(item)
        except EdgeUnavailable:
            return self._fetch_http(item)

    @staticmethod
    def _date(text: str) -> datetime | None:
        try:
            return datetime.strptime(text, "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def extract_html(self, html: str, source_url: str) -> list[ExtractedRecord]:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text)

        # The labelled figure, and the date the page puts on it.
        #
        # The date matters as much as the price. ECHEMI leaves yesterday's table up until it
        # publishes today's, so a visit on the 9th can find a table still dated the 8th. Dating that
        # reading by the day it was collected files an 8 September price as a 9 September one and
        # invents a flat day; dating it by the page's own date makes it a duplicate of a reading
        # already stored, which the loader drops. The store has both faults today: the 9 September
        # ECHEMI polyol row repeats the 8th's price under the 9th's date.
        labelled = self._LABELLED.search(text)
        if labelled:
            return [self._record(
                source_url,
                price=float(labelled.group("price").replace(",", "")),
                dt=self._date(labelled.group("date")),
                currency="CNY",
            )]

        # Match date-before-price or price-before-date layouts.
        patterns = [
            re.compile(rf"({self._DATE_RE}).{{0,120}}?([0-9]+(?:\.[0-9]+)?)\s*(Yuan/(?:mt|ton)|USD/(?:mt|ton))", re.I),
            re.compile(rf"([0-9]+(?:\.[0-9]+)?)\s*(Yuan/(?:mt|ton)|USD/(?:mt|ton)).{{0,120}}?({self._DATE_RE})", re.I),
        ]
        candidates = []
        for p in patterns:
            m = p.search(text)
            if m:
                if p is patterns[0]:
                    dt, price, unit_text = self._date(m.group(1)), float(m.group(2)), m.group(3)
                else:
                    price, unit_text, dt = float(m.group(1)), m.group(2), self._date(m.group(3))
                candidates.append((dt, price, unit_text))
        if not candidates:
            return []
        dt, price, unit_text = candidates[0]
        currency = "CNY" if unit_text.lower().startswith("yuan") else "USD"
        return [self._record(source_url, price=price, dt=dt, currency=currency)]

    def _record(self, source_url: str, *, price: float, dt, currency: str) -> ExtractedRecord:
        data = {
            "type": "price", "product": self.product, "grade": self.grade,
            "market_geography": self.geography, "commercial_basis": self.basis,
            "packaging": "UNSPECIFIED", "price": price, "currency": currency,
            "unit": "t", "observation_type": price_kinds.ASSESSED, "observation_date": dt,
        }
        return ExtractedRecord(kind="price", source_url=source_url, publication_date=dt, data=data)
