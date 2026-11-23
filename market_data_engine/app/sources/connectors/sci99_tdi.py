from __future__ import annotations

import html as html_lib
import json
import re

import httpx

from app.domain.schemas import SourceItem
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector


class Sci99TDIConnector(PublicTDIExcerptConnector):
    """SCI99 (卓创资讯) TDI national mainstream market average (甲苯二异氰酸酯全国主流市场均价).

    The public monitor page shows a 7-day price table to any visitor, but the table is
    filled client-side from the page's own JSON call
    ``/priceMonitor/listProductPagePrice?oldId=375&type=0`` (no login, no token;
    verified in a real browser on 2026-09-08). The server HTML therefore never contains
    the numbers, which is why the previous text parser found nothing. We fetch the page
    (evidence + session cookies) and then that same JSON call, and store both together.
    """

    source_id = "sci99_tdi"
    source_url = "https://www.sci99.com/monitor-375-0.html"
    prices_url = "https://www.sci99.com/priceMonitor/listProductPagePrice?oldId=375&type=0"
    _JSON_RE = re.compile(r'<script type="application/json" data-sci99-prices>(.*?)</script>', re.S)

    def fetch_item(self, item: SourceItem) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            page = client.get(item.source_url)
            page.raise_for_status()
            prices = client.get(
                self.prices_url,
                headers={"Referer": item.source_url, "Accept": "application/json, text/plain, */*",
                         "X-Requested-With": "XMLHttpRequest"},
            )
            prices.raise_for_status()
            payload = prices.text
        # Keep the JSON verbatim next to the page so raw evidence shows exactly what was read.
        return (
            page.text
            + '\n<script type="application/json" data-sci99-prices>'
            + html_lib.escape(payload, quote=False)
            + "</script>\n"
        ).encode("utf-8")

    def extract_html(self, html: str, source_url: str):
        m = self._JSON_RE.search(html)
        if m:
            try:
                data = json.loads(html_lib.unescape(m.group(1)))
            except json.JSONDecodeError:
                data = {}
            out = []
            for row in (data.get("data") or []):
                dt = self.iso_date(str(row.get("dateRange", "")))
                try:
                    price = float(str(row.get("mdataValue", "")).replace(",", ""))
                except ValueError:
                    continue
                if dt is None or price <= 0:
                    continue
                record = self.record(source_url, price=price, dt=dt)
                # The page does not state a grade; keep the identity aligned with SunSirs/Longzhong (TDI, no grade).
                record.data["product"] = "TDI"
                record.data["grade"] = None
                out.append(record)
            if out:
                return out
        # Fallback: rendered visible text of the table ("2026-09-07 16850.00 0.00 0.00% 16690.00").
        text = self.visible_text(html)
        if not re.search(r"TDI|甲苯二异氰酸酯", text, re.I):
            return []
        out = []
        for m in re.finditer(r"(20\d{2}-\d{2}-\d{2})\s+([0-9]{4,6}(?:\.[0-9]+)?)\s+[-+0-9.]+\s+[-+0-9.]+%", text):
            record = self.record(source_url, price=float(m.group(2)), dt=self.iso_date(m.group(1)))
            record.data["product"] = "TDI"
            record.data["grade"] = None
            out.append(record)
        return out
