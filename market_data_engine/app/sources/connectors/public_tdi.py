from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds


class PublicTDIExcerptConnector:
    """Source-specific public/excerpt reader with no auth/paywall bypass."""
    allow_empty_parse = True
    source_id = ""
    source_url = ""

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        response = httpx.get(item.source_url, follow_redirects=True, timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return response.content

    @staticmethod
    def visible_text(html: str) -> str:
        return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()

    @staticmethod
    def record(source_url: str, *, price: float, dt: datetime | None, geography: str = "China", currency: str = "CNY", basis: str = "EXW", grade: str | None = None, packaging: str | None = None) -> ExtractedRecord:
        return ExtractedRecord(kind="price", source_url=source_url, publication_date=dt, data={
            "type": "price", "product": "TDI 80/20", "grade": grade,
            "market_geography": geography, "commercial_basis": basis, "packaging": packaging,
            "price": price, "currency": currency, "unit": "t", "observation_type": price_kinds.ASSESSED,
            "observation_date": dt,
        })

    @staticmethod
    def iso_date(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
