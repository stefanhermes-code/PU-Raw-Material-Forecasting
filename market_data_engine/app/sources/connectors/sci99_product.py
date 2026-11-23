"""SCI99 (卓创资讯) daily market average for a configured product.

`Sci99TDIConnector` carries TDI and its own page shape. This is the same acquisition, parameterised:
SCI99 publishes every monitored product the same way - a monitor page whose table is filled
client-side from `/priceMonitor/listProductPagePrice?oldId=<id>&type=0`, no login and no token - so
a second product costs an id, not a connector.

Why it exists (18 September 2026): the governed Chinese polyether series had one live publisher,
Longzhong, after ECHEMI stopped answering. SunSirs prices no polyol at all - 210 commodities, none of
them - and Baiinfo and PUdaily are both behind paid logins. SCI99 prices five polyethers, and 卓创 is
independent of both Longzhong and SunSirs, so it is a publisher that can actually corroborate.

Checked against the store the same day: SCI99 软泡聚醚 read 12,587.50 CNY/t while Longzhong read
12,550 - 0.3% apart, the same market.
"""
from __future__ import annotations

import html as html_lib
import json
import re

import httpx

from app.domain.schemas import SourceItem
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector


class Sci99ProductConnector(PublicTDIExcerptConnector):
    """Configured SCI99 monitor reader. Public page, no login; attribution preserved."""

    _JSON_RE = re.compile(r'<script type="application/json" data-sci99-prices>(.*?)</script>', re.S)

    def __init__(self, *, source_id: str, old_id: str, product: str,
                 grade: str | None = None, geography: str = "China", basis: str = "EXW",
                 currency: str = "CNY"):
        self.source_id = source_id
        self.old_id = str(old_id)
        self.source_url = f"https://www.sci99.com/monitor-{self.old_id}-0.html"
        self.prices_url = (
            f"https://www.sci99.com/priceMonitor/listProductPagePrice?oldId={self.old_id}&type=0"
        )
        self.product = product
        self.grade = grade
        self.geography = geography
        self.basis = basis
        self.currency = currency

    def fetch_item(self, item: SourceItem) -> bytes:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            page = client.get(item.source_url)
            page.raise_for_status()
            prices = client.get(
                self.prices_url,
                headers={"Referer": item.source_url,
                         "Accept": "application/json, text/plain, */*",
                         "X-Requested-With": "XMLHttpRequest"},
            )
            prices.raise_for_status()
            payload = prices.text
        # The JSON is kept verbatim beside the page so the raw evidence is exactly what was read.
        return (
            page.text
            + '\n<script type="application/json" data-sci99-prices>'
            + html_lib.escape(payload, quote=False)
            + "</script>\n"
        ).encode("utf-8")

    def extract_html(self, html: str, source_url: str):
        found = self._JSON_RE.search(html)
        if not found:
            return []
        try:
            data = json.loads(html_lib.unescape(found.group(1)))
        except json.JSONDecodeError:
            return []

        out = []
        for row in (data.get("data") or []):
            day = self.iso_date(str(row.get("dateRange", "")))
            try:
                price = float(str(row.get("mdataValue", "")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if day is None or price <= 0:
                continue
            record = self.record(source_url, price=price, dt=day,
                                 geography=self.geography, currency=self.currency,
                                 basis=self.basis, grade=self.grade)
            # `record` is shaped for TDI. The product decides the series, so it is set here.
            record.data["product"] = self.product
            record.data["grade"] = self.grade
            out.append(record)
        return out
