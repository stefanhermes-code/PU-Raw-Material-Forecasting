from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.connectors.mysteel_pu import PU_PRICE_PAGE, parse_pu_price_block
from raw_material_forecasting import price_kinds


# A published price table: date, quote, change, change rate. The same shape the TDI connector reads,
# and the only shape on an Oilchem page that is a price rather than a headline about one.
_PRICE_TABLE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+([0-9]+(?:\.[0-9]+)?)\s+"
    r"(?:[-+]?[0-9]+(?:\.[0-9]+)?|0)\s+(?:[-+]?[0-9]+(?:\.[0-9]+)?%|0%)"
)


class OilchemProductConnector:
    """Visible-page Longzhong/Oilchem market-article collector for a configured PU product."""

    allow_empty_parse = True

    def __init__(
        self,
        *,
        source_id: str,
        source_url: str,
        product: str,
        grade: str | None,
        geography: str,
        basis: str,
        title_token: str,
        mysteel_family: str | None = None,
    ):
        self.source_id = source_id
        self.source_url = source_url
        self.product = product
        self.grade = grade
        self.geography = geography
        self.basis = basis
        self.title_token = title_token
        # Mysteel's polyurethane block (same publisher: Mysteel owns Longzhong) states this
        # product's dated CNY/t price plainly, where the list.oilchem.net article page does not.
        self.mysteel_family = mysteel_family

    def discover(self) -> list[SourceItem]:
        items = [SourceItem(source_url=self.source_url)]
        if self.mysteel_family:
            items.append(SourceItem(source_url=PU_PRICE_PAGE))
        return items

    def historical_listing_items(self, pages: int = 100) -> list[SourceItem]:
        base = self.source_url.rstrip("/")
        return [SourceItem(source_url=f"{base}/{page}.html") for page in range(1, pages + 1)]

    def discover_history_html(self, html: str, *, start: date, end: date) -> list[SourceItem]:
        token = re.escape(self.title_token)
        pattern = re.compile(
            rf'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*{token}[^<]*)</a>\s*\[(20\d{{2}}-\d{{2}}-\d{{2}})',
            re.I,
        )
        found: list[str] = []
        for href, _title, date_text in pattern.findall(html):
            dt = datetime.strptime(date_text, "%Y-%m-%d").date()
            if start <= dt <= end:
                url = urljoin("https://list.oilchem.net", href)
                if url not in found:
                    found.append(url)
        return [SourceItem(source_url=url) for url in found]

    def fetch_item(self, item: SourceItem) -> bytes:
        r = httpx.get(item.source_url, follow_redirects=True, timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.content

    def extract_html(self, html: str, source_url: str) -> list[ExtractedRecord]:
        # Mysteel polyurethane block: one dated CNY/t price for this product.
        if self.mysteel_family:
            for row in parse_pu_price_block(html):
                if row["product_family"] != self.mysteel_family:
                    continue
                return [ExtractedRecord(
                    kind="price",
                    source_url=source_url,
                    publication_date=row["observation_date"],
                    data={
                        "type": "price",
                        "product": self.product,
                        "grade": self.grade,
                        "market_geography": self.geography,
                        "commercial_basis": self.basis,
                        "price": row["price"],
                        "currency": row["currency"],
                        "unit": row["unit"],
                        "observation_type": price_kinds.ASSESSED,
                        "observation_date": row["observation_date"],
                        "source_label": f"Mysteel/Longzhong polyurethane market price, {row['label']}",
                    },
                )]

        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()
        if self.title_token.lower() not in text.lower() and self.product.lower() not in text.lower():
            return []

        # A published price table, and nothing else.
        #
        # This used to take any four-to-six digit number followed by 元/吨 anywhere on the page. On
        # the polymeric MDI list page the only such number is a headline - "[聚合MDI]：北方某大厂9月
        # 中旬一口价执行18500元/吨", one northern producer's fixed price for mid-September - and that
        # page carries no price table at all. So the connector filed a producer's announcement as
        # Longzhong's market price, and because the headline stayed at the top of the list it filed
        # the same 18,500 again every day from 11 to 16 September 2026: six of twelve readings, one
        # announcement, outvoting the real market price of 18,000.
        #
        # A producer's asking price is worth having. It is evidence of what a producer wants, which
        # belongs in the producer-signals lane, and it is not evidence of what the market paid.
        records: list[ExtractedRecord] = []
        seen: set[tuple[str, float]] = set()
        for date_text, price_text in _PRICE_TABLE.findall(text):
            key = (date_text, float(price_text))
            if key in seen:
                continue
            seen.add(key)
            observed = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            records.append(ExtractedRecord(
                kind="price",
                source_url=source_url,
                publication_date=observed,
                data={
                    "type": "price",
                    "product": self.product,
                    "grade": self.grade,
                    "market_geography": self.geography,
                    "commercial_basis": self.basis,
                    "packaging": "UNSPECIFIED",
                    "price": float(price_text),
                    "currency": "CNY",
                    "unit": "t",
                    "observation_type": price_kinds.ASSESSED,
                    "observation_date": observed,
                },
            ))
        return records
