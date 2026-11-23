"""SCI99 (卓创资讯) polymeric MDI national mainstream market price (聚合MDI).

The same machinery as the TDI connector, pointed at a different monitor. SCI99's public monitor page
shows a seven-day price table to any visitor and fills it client-side from the page's own JSON call,
`/priceMonitor/listProductPagePrice?oldId=<monitor>&type=0` - no login and no token. TDI is monitor
375; polymeric MDI is 384.

Why a third publisher at all: the consolidated value takes one value per source and then the mean, so
with two publishers it is a two-point average and either of them moving alone moves the market price
by half of that move. TDI China has four. MDI had two, and SunSirs is not going to be the third - it
carries MDI at prodetail-675 and the last price on that page is 31 July 2018, so wiring it up would
inject a seven-year-old 22,500 into a daily series.

This has NOT been run against the live endpoint. Neither the cloud sandbox nor the local Linux VM can
reach sci99.com - both are refused 403 by their egress proxy - so the first collection on the
Windows machine is what confirms the monitor id. If 384 is wrong the run reports zero records rather
than a wrong number, because every price here comes from the JSON payload or a dated table row and
nothing is inferred.
"""
from __future__ import annotations

import html as html_lib
import json
import re

import httpx

from app.domain.schemas import SourceItem
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector


class Sci99PMDIConnector(PublicTDIExcerptConnector):
    """SCI99 polymeric MDI, from the same public monitor endpoint the TDI connector reads."""

    source_id = "sci99_pmdi"
    source_url = "https://www.sci99.com/monitor-384-0.html"
    prices_url = "https://www.sci99.com/priceMonitor/listProductPagePrice?oldId=384&type=0"
    product = "Polymeric MDI"
    # What the page calls it, in either script.
    page_tokens = ("聚合MDI", "PMDI", "polymeric MDI")
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
        # The JSON is kept verbatim beside the page so the raw evidence shows exactly what was read.
        return (
            page.text
            + '\n<script type="application/json" data-sci99-prices>'
            + html_lib.escape(payload, quote=False)
            + "</script>\n"
        ).encode("utf-8")

    def _record(self, source_url: str, *, price: float, dt):
        record = self.record(source_url, price=price, dt=dt)
        # The identity has to match Longzhong's and ECHEMI's or the store makes a third market of
        # it: polymeric MDI, China, ex-works, yuan, no grade qualifier.
        record.data["product"] = self.product
        record.data["grade"] = None
        return record

    def extract_html(self, html: str, source_url: str):
        found = self._JSON_RE.search(html)
        if found:
            try:
                data = json.loads(html_lib.unescape(found.group(1)))
            except json.JSONDecodeError:
                data = {}
            out = []
            for row in (data.get("data") or []):
                observed = self.iso_date(str(row.get("dateRange", "")))
                try:
                    price = float(str(row.get("mdataValue", "")).replace(",", ""))
                except ValueError:
                    continue
                if observed is None or price <= 0:
                    continue
                out.append(self._record(source_url, price=price, dt=observed))
            if out:
                return out

        # Fallback: the rendered table, "2026-09-16 18000.00 0.00 0.00% 17900.00".
        text = self.visible_text(html)
        if not any(token.lower() in text.lower() for token in self.page_tokens):
            return []
        return [
            self._record(source_url, price=float(match.group(2)), dt=self.iso_date(match.group(1)))
            for match in re.finditer(
                r"(20\d{2}-\d{2}-\d{2})\s+([0-9]{4,6}(?:\.[0-9]+)?)\s+[-+0-9.]+\s+[-+0-9.]+%", text)
        ]
