"""SunSirs daily spot price for a configured chemical.

`SunSirsTDIConnector` also carries the event lane - the news index that makes signal corroboration
possible - and that lane is TDI-specific. A feedstock needs none of it: toluene is collected for its
price series, not for plant announcements. So this is the price half only, parameterised by product.

The daily table is the same shape on every `prodetail-*` page: a product name, the word "Chemical",
a price in CNY per tonne, and an ISO date. Only the product token changes.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import requests

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds


class SunSirsProductConnector:
    """Configured SunSirs visible daily-spot connector. Public page; attribution preserved."""

    base_url = "https://www.sunsirs.com"

    def __init__(self, *, source_id: str, source_url: str, product: str,
                 product_token: str | None = None, grade: str | None = None,
                 geography: str = "China", basis: str = "EXW"):
        self.source_id = source_id
        self.source_url = source_url
        self.product = product
        # What the page calls it, which is not always what we call it.
        self.product_token = product_token or product
        self.grade = grade
        self.geography = geography
        self.basis = basis

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

    def _request(self, url: str) -> bytes:
        session = requests.Session()
        session.headers.update(self._headers())
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            # Windows/Python can inherit a proxy path the browser does not use.
            direct = requests.Session()
            direct.trust_env = False
            direct.headers.update(self._headers())
            response = direct.get(url, timeout=30)
            response.raise_for_status()
            return response.content

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        return self._request(item.source_url)

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()

        pattern = re.compile(
            rf"{re.escape(self.product_token)}\s+Chemical\s+([0-9,]+(?:\.[0-9]+)?)\s+(20\d{{2}}-\d{{2}}-\d{{2}})\b",
            re.I,
        )
        records: list[ExtractedRecord] = []
        for price_text, date_text in pattern.findall(text):
            observation = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            data = {
                "type": "price",
                "product": self.product,
                "market_geography": self.geography,
                "commercial_basis": self.basis,
                "price": float(price_text.replace(",", "")),
                "currency": "CNY",
                "unit": "t",
                "observation_type": price_kinds.ASSESSED,
                "observation_date": observation,
            }
            if self.grade:
                data["product_grade_or_chemistry"] = self.grade
            records.append(ExtractedRecord(kind="price", source_url=src,
                                           publication_date=observation, data=data))
        return records
