"""Shared parser for Mysteel's public polyurethane price block.

Mysteel (Shanghai Ganglian, 上海钢联) publishes a free, server-rendered block titled
"聚氨酯主流产品市场价格" (mainstream polyurethane product market prices) on
https://www.mysteel.com/oilchem/juanzhi/ , carrying one dated CNY/t price per product:

    <h2 ...>聚氨酯主流产品市场价格</h2>
    <div class="marketime">2026-09-08</div>
    <ul class="market-price-list">
      <li class="price-item"><h4 class="product-name">TDI</h4> ... <h5>16800</h5>

IMPORTANT - source independence. Mysteel's chemical desk *is* Longzhong: the section path
is /oilchem/, every article is bylined "来源：隆众资讯" (source: Longzhong Information), and
the TDI figure here matches the number our list.oilchem.net collector already produces.
This page is therefore a better *page* for the existing Longzhong source, not a new
independent publisher, and must never be registered as a separate source - doing so would
inflate the independent-source count that drives the HTC verification level.
Verified 2026-09-09.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timezone

PU_PRICE_PAGE = "https://www.mysteel.com/oilchem/juanzhi/"
BLOCK_MARKER = "聚氨酯主流产品市场价格"

_DATE_RE = re.compile(r'class="marketime"[^>]*>\s*(?P<date>20\d{2}-\d{2}-\d{2})')
_ITEM_RE = re.compile(
    r'class="product-name"[^>]*>\s*(?P<name>[^<]+?)\s*</h4>.*?<h5>\s*(?P<price>[0-9][0-9,]*(?:\.[0-9]+)?)\s*</h5>',
    re.S,
)

# Mysteel product label -> (product family used by the application, grade or None)
PRODUCT_MAP: dict[str, tuple[str, str | None]] = {
    "TDI": ("TDI", None),
    "聚合MDI": ("PMDI", "PMDI"),
    "纯MDI": ("PURE_MDI", None),
    "软泡聚醚": ("POLYOL", "FLEXIBLE_SLABSTOCK_POLYETHER"),
    "环氧丙烷": ("PO", None),
    "DMF": ("DMF", None),
    "己二酸": ("ADIPIC_ACID", None),
    "新戊二醇": ("NPG", None),
    "BDO": ("BDO", None),
}


def parse_pu_price_block(html: str) -> list[dict]:
    """Every dated product price in the block, as plain dicts. Empty list if the block is absent."""
    if BLOCK_MARKER not in html:
        return []
    date_match = _DATE_RE.search(html)
    if not date_match:
        return []
    observed = datetime.strptime(date_match.group("date"), "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # Only look inside the price list, so unrelated <h5> elements elsewhere cannot leak in.
    start = html.index(BLOCK_MARKER)
    end = html.find("</ul>", start)
    scope = html[start : end if end != -1 else len(html)]

    out: list[dict] = []
    seen: set[str] = set()
    for match in _ITEM_RE.finditer(scope):
        label = html_lib.unescape(match.group("name")).strip()
        # "BDO华东" and friends carry a regional suffix; match on the leading product token.
        key = next((k for k in PRODUCT_MAP if label == k), None) or next(
            (k for k in PRODUCT_MAP if label.startswith(k)), None
        )
        if key is None or key in seen:
            continue
        seen.add(key)
        family, grade = PRODUCT_MAP[key]
        out.append({
            "label": label,
            "product_family": family,
            "grade": grade,
            "price": float(match.group("price").replace(",", "")),
            "currency": "CNY",
            "unit": "t",
            "observation_date": observed,
        })
    return out


# --------------------------------------------------------------------------- event stream
# The same free polyurethane page carries two title streams that are the market's early-warning
# material: producer price announcements (settlement / listed / spot flashes from named plants) and
# plant events (shutdowns, maintenance brought forward). The title carries the signal on its own - "西北某TDI工厂停车检修" tells you a Northwest TDI
# plant is down whatever the body says. Weekly capacity-utilisation and producer-margin postings are
# the other two classic precursors of a price move.
#
# This parser stores only the headline, its date, its link and the publisher attribution.
#
# CORRECTION, 17 November 2026. The note above used to say article bodies are member-gated.
# They are not: the article URL answers 200 with no login and no member check, and serves the
# opening of the body before cutting it off with a single "…". For the weekly plant tables that
# preview is four or five plants with capacity, load and outage dates - the supply-side signal
# this file said was unavailable. `longzhong_plant_table` reads it. The claim was never measured
# when it was written, and it kept the most useful thing on the page out of the store.
#
# NOTE: the intraday 快讯 items are a rolling feed and rotate out within a day. This must be
# collected daily; there is no backfill.

_MATERIALS: tuple[tuple[str, str], ...] = (
    ("TDI", "TDI"), ("聚合MDI", "PMDI"), ("纯MDI", "PURE_MDI"), ("MDI-50", "MDI_50"),
    ("软泡聚醚", "POLYOL"), ("硬泡聚醚", "POLYOL"), ("POP聚醚", "POLYOL"), ("高回弹聚醚", "POLYOL"),
    ("聚醚多元醇", "POLYOL"), ("聚醚", "POLYOL"),
    ("环氧丙烷", "PO"), ("环氧乙烷", "EO"), ("DMF", "DMF"), ("己二酸", "ADIPIC_ACID"),
    ("新戊二醇", "NPG"), ("BDO", "BDO"), ("TMP", "TMP"), ("TPU", "TPU"), ("苯胺", "ANILINE"),
    ("液氯", "LIQUID_CHLORINE"),
    # THF, under both spellings. Measured 29 October 2026: five plant-event headlines sat with no
    # material at all because of this. THF is not a family the store prices and not a node in the
    # route graph, so these reach no forecast and should not - but the Market Intelligence page can
    # now say what they are about instead of showing a plant event for nothing.
    ("四氢呋喃", "THF"), ("THF", "THF"),
    # Last: the bare Latin tickers, so the specific Chinese labels above always win (POP聚醚 is a
    # polyol, not propylene oxide; 聚合MDI is pMDI, not a bare MDI). Industry-news headlines write
    # plant capacity as "30万吨/年PO装置", and a roundup as "国内MDI装置动态汇总" - which named no
    # material until this, because every MDI label here was a specific grade.
    #
    # Bare MDI is pMDI: Stefan, 29 October 2026, "for now I take the decision to keep them as pMDI".
    # Same ruling as migration 0063.
    ("PO", "PO"), ("MDI", "PMDI"),
)

# Ordered: the first pattern that matches wins.
_EVENT_RULES: tuple[tuple[str, str, str], ...] = (
    ("PLANT_EVENT", "outage", r"停车|检修|装置|开车|故障|停工|复产"),
    ("PRODUCER_PRICE", "producer_price_announcement", r"结算价|结挂价|挂牌|价格快报|价格快讯|价格复报|价格上调|价格下调|竞价|分销渠道|直销渠道"),
    ("CAPACITY", "capacity_utilisation", r"产能利用率|开工率"),
    ("MARGIN", "producer_margin", r"利润|毛利"),
)
_SKIP = re.compile(r"早间提示|日评|周评|月评|聚焦|行情快讯|价格行情")
# Mysteel article URLs are /oilchem/a/<YYMMDDHH>/<hash>.html . Section links (/oilchem/article/...)
# are navigation, not events - requiring the article form keeps headers like "企业装置动态" out.
_ARTICLE_HREF_RE = re.compile(r"/oilchem/a/(?P<stamp>\d{6})\d{2}/", re.I)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>[^<]{6,120})</a>', re.I)
_EMBEDDED_DATE_RE = re.compile(r"（(20\d{6})）|\((20\d{6})\)")
_SHORT_DATE_RE = re.compile(r"\[(\d{2})-(\d{2})\]")


def _material_of(title: str) -> str | None:
    for label, family in _MATERIALS:
        if label in title:
            return family
    return None


def _classify_event(title: str) -> tuple[str, str] | None:
    for kind, record_type, pattern in _EVENT_RULES:
        if re.search(pattern, title):
            return kind, record_type
    return None


def parse_pu_events(html: str, *, today: date | None = None, base_url: str = "https://www.mysteel.com") -> list[dict]:
    """Producer-price, plant and fundamentals headlines from the polyurethane page.

    Returns dicts with headline / kind / record_type / material / event_date / url.
    Pure market commentary (早间提示, 日评, 周评, 聚焦) is skipped - it is narrative, not an event.
    """
    today = today or datetime.now(timezone.utc).date()
    out: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(html):
        title = html_lib.unescape(match.group("title")).strip()
        if not title or title in seen or _SKIP.search(title):
            continue
        classified = _classify_event(title)
        if classified is None:
            continue
        kind, record_type = classified
        seen.add(title)

        href_raw = html_lib.unescape(match.group("href"))
        article = _ARTICLE_HREF_RE.search(href_raw)
        if article is None:
            continue   # navigation link, not an article

        # The article URL carries YYMMDD - the most reliable date on the page.
        try:
            event_date = datetime.strptime(article.group("stamp"), "%y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            event_date = None

        embedded = _EMBEDDED_DATE_RE.search(title)
        if event_date is not None:
            pass
        elif embedded:
            stamp = embedded.group(1) or embedded.group(2)
            event_date = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            window = html[max(0, match.start() - 260) : match.start()]
            short = _SHORT_DATE_RE.findall(window)
            if short:
                month, day = int(short[-1][0]), int(short[-1][1])
                year = today.year - 1 if month > today.month + 1 else today.year
                try:
                    event_date = datetime(year, month, day, tzinfo=timezone.utc)
                except ValueError:
                    event_date = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
            else:
                event_date = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

        href = href_raw
        out.append({
            "headline": title,
            "kind": kind,
            "record_type": record_type,
            "material": _material_of(title),
            "event_date": event_date,
            "url": href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/"),
        })
    return out
