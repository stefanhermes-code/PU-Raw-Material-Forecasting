"""Chinese polyester polyol, read from Longzhong's East China market feed.

THE HOLE THIS FILLS. The store prices eleven PU raw materials and polyester polyol is the one it
cannot price at all: the only Chinese series is a purchase record that stops in May 2017, and the
European one is a buyer's book that stops in January 2017. Nine years with nothing live. SunSirs
prices no polyester, SCI99's monitor does not carry it, and the two that do are behind paid logins.

WHAT THE PAGE IS. Not a table. Longzhong publishes East China polyester polyol inside a dated news
feed - each entry a short market report, timestamped to the minute, carrying the day's negotiation
ranges by grade:

    2026-09-28 10:26 ... AA/BG 商谈价13000-13500元/吨左右, AA/EG 商谈价12300-12500元/吨,
                         AA/BG, EG 商谈价12500-12700元/吨, 报盘持稳

So the parser reads prose, not cells. It works on the page's text with the markup stripped, which
is what makes it survive a layout change: the numbers and the grade labels are what it looks for.

THE GRADES. AA/BG, AA/EG and AA/BG+EG are adipic-acid polyesters - the aliphatic family - named by
the glycol they are condensed with. They are three grades of one market and are kept as three
series, because a buyer of AA/BG is not quoted the AA/EG price.

WHAT IS STORED. The range as published, low and high, and the midpoint as the reading. The midpoint
is arithmetic on two published numbers, not an assessment of our own.

A NOTE ON HOW THIS WAS BUILT, 29 September 2026. The page is reachable from the machine the engine
runs on but not from where this was written, so the parser was built and tested against the entry
text rather than against live markup. The first live run is what proves it; a run that parses
nothing is recorded as a failure rather than passing silently.
"""
from __future__ import annotations

import re

import httpx

from app.domain.schemas import SourceItem
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector

SOURCE_URL = "https://www.mysteel.com/hot/1650311.html"

_TAGS = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"(?P<day>20\d{2}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?")
# "AA/BG 商谈价13000-13500元/吨" and the same line with the two glycols enumerated.
_PRICE = re.compile(
    r"(?P<grade>AA\s*/\s*BG\s*[、,+]\s*EG|AA\s*/\s*BG|AA\s*/\s*EG)"
    r"[^0-9]{0,24}?(?P<low>\d{4,6})\s*[-~]\s*(?P<high>\d{4,6})\s*元\s*/\s*吨",
    re.I)

GRADES = {"AA/BG": "AA/BG", "AA/EG": "AA/EG", "AA/BG+EG": "AA/BG+EG"}


def _grade(raw: str) -> str:
    cleaned = re.sub(r"\s+", "", raw).upper().replace("、", "+").replace(",", "+")
    return GRADES.get(cleaned, cleaned)


def polyester_prices(page: str) -> list[dict]:
    """Every dated East China polyester polyol range on the page.

    Entries are split on their own timestamp, so a price never takes the date of the entry above
    it. An entry with no readable date is skipped rather than stamped with today.
    """
    text = _TAGS.sub(" ", page)
    text = text.replace("&nbsp;", " ")
    stamps = list(_STAMP.finditer(text))
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for index, stamp in enumerate(stamps):
        end = stamps[index + 1].start() if index + 1 < len(stamps) else len(text)
        entry = text[stamp.end():end]
        for found in _PRICE.finditer(entry):
            low = float(found.group("low"))
            high = float(found.group("high"))
            if low <= 0 or high < low:
                continue
            grade = _grade(found.group("grade"))
            key = (stamp.group("day"), grade)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "day": stamp.group("day"),
                "grade": grade,
                "low": low,
                "high": high,
                "price": round((low + high) / 2, 2),
            })
    return out


class MysteelPolyesterConnector(PublicTDIExcerptConnector):
    """East China polyester polyol, three grades, from the public feed."""

    source_id = "longzhong_polyester"
    source_url = SOURCE_URL
    product = "ALIPHATIC_POLYESTER_POLYOL"
    geography = "East China"
    basis = "EXW"
    currency = "CNY"

    def fetch_item(self, item: SourceItem) -> bytes:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            page = client.get(item.source_url)
            page.raise_for_status()
            return page.text.encode("utf-8")

    def extract_html(self, html: str, source_url: str):
        out = []
        for row in polyester_prices(html):
            day = self.iso_date(row["day"])
            if day is None:
                continue
            record = self.record(source_url, price=row["price"], dt=day,
                                 geography=self.geography, currency=self.currency,
                                 basis=self.basis, grade=row["grade"])
            record.data["product"] = self.product
            record.data["grade"] = row["grade"]
            record.data["price_low"] = row["low"]
            record.data["price_high"] = row["high"]
            out.append(record)
        return out
