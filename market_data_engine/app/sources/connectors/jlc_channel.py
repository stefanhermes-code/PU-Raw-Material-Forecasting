"""金联创 (JLC) polyurethane channel: one producer price per product, free of charge.

JLC's full producer table is behind a login - every cell reads 未登录 with a sales number beside it -
but its channel page carries a 数据专区 block giving one named producer's price per product, updated
daily. Six of the seven products in that block are chemicals in our route graph, so all six are read
here and DMF, which is not, is left alone. Stefan's rule, 19 September 2026: if a chemical is in the
routes, capture it.

These are PRODUCER prices. `observation_type` is what keeps them in their own lane, and the delivery
term is recorded as what it is: 挂牌价 is a list price, 现金出厂 cash
ex-works, 现金送到 cash delivered, 承兑自提 and 承兑送到
acceptance terms. They are kept apart because they are a different quantity from an assessed
regional market price - averaging one producer's cash ex-works into a market assessment produces a
number nobody trades - and not because a producer's price counts for less. In polyurethane a
producer's posting frequently leads the market rather than follows it, and the spread between the
posting and the assessment is a signal in its own right. Stefan, 19 September 2026, correcting me:
the PRODUCER_DIRECT rule that a publisher cannot corroborate a claim about itself is about EVENTS -
Wanhua announcing a Wanhua outage - and does not carry across to prices.

The block carries no date of its own, so a reading is stamped with the day it was collected. That is
honest for a page that shows today's posting and nothing else, and it is why this is a DAILY_CHECK.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds

# The page's product name -> what we call it. The resolver turns each into a route-graph family.
# DMF is priced in the same block and is deliberately absent: it is not in the routes.
PRODUCTS: dict[str, str] = {
    "普通软泡聚醚": "Polyether polyol flexible foam",
    "聚合MDI": "Polymeric MDI",
    "TDI": "TDI",
    "环氧丙烷": "Propylene oxide",
    "苯胺": "Aniline",
    "己二酸": "Adipic acid",
}

# What JLC calls the price, and the delivery term it carries.
#
# These were five invented basis codes, none of them an Incoterm, each folding the way a price is
# paid into the field that holds a delivery term. JLC states the delivery term itself in four of the
# five: 出厂 is ex-factory, 自提 is the buyer collecting, and both are EXW; 送到 is delivered to the
# buyer, which is DAP. 挂牌价 is a list price and states nothing - and a producer quoting its own
# list price is quoting at its own gate. Stefan, 21 September 2026: if a producer shows no Incoterm,
# assume EXW, there is nothing else it can be.
#
# 现金 cash and 承兑 acceptance-bill are dropped rather than recorded. Stefan's call the same day.
# The consequence, should a producer ever post both for one chemical on one day: they land on one
# identity, and the later reading supersedes the earlier one under the revision rule.
BASES: dict[str, str] = {
    "挂牌价": "EXW",
    "现金出厂": "EXW",
    "现金送到": "DAP",
    "承兑自提": "EXW",
    "承兑送到": "DAP",
}

UNIT_LINE = "元/吨"
TABLE_HEADER = "产品名称"
TAG = re.compile(r"<[^>]+>")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class JlcChannelConnector:
    """Visible-page JLC channel reader. Public page, no login; attribution preserved."""

    allow_empty_parse = True
    source_id = "jlc_pu_channel"
    source_url = "https://pu.315i.com/"

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        response = httpx.get(item.source_url, follow_redirects=True, timeout=30.0, headers=headers)
        response.raise_for_status()
        return response.content

    @staticmethod
    def visible_lines(html: str) -> list[str]:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        text = TAG.sub("\n", text).replace("&nbsp;", " ").replace("&amp;", "&")
        return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _price(text: str) -> float | None:
        """A cell is a number, a range like 13700～13700, or a dash on a day with no posting."""
        found = NUMBER.findall(text.replace(",", ""))
        if not found:
            return None
        values = [float(v) for v in found]
        price = sum(values) / len(values)
        return price if price > 0 else None

    def extract_html(self, html: str, source_url: str) -> list[ExtractedRecord]:
        lines = self.visible_lines(html)
        try:
            start = lines.index(TABLE_HEADER)
        except ValueError:
            return []

        collected = datetime.now(timezone.utc)
        out: list[ExtractedRecord] = []
        index = start
        seen: set[str] = set()
        # Walk the block: product, producer, price type, price, change..., 元/吨 ends the row.
        while index < len(lines) - 3:
            index += 1
            token = lines[index]
            if token not in PRODUCTS or token in seen:
                continue
            producer, basis_text, price_text = lines[index + 1], lines[index + 2], lines[index + 3]
            if basis_text not in BASES:
                # A row with no posting today prints a dash where the price type goes.
                continue
            price = self._price(price_text)
            if price is None:
                continue
            seen.add(token)
            out.append(ExtractedRecord(
                kind="price", source_url=source_url, publication_date=collected,
                data={
                    "type": "price",
                    "product": PRODUCTS[token],
                    "grade": None,
                    "market_geography": "China",
                    "commercial_basis": BASES[basis_text],
                    "packaging": None,
                    "price": price,
                    "currency": "CNY",
                    "unit": "t",
                    "observation_type": price_kinds.ASK,
                    "observation_date": collected,
                    "producer": producer,
                },
            ))
        return out
