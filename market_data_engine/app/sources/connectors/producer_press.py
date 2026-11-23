"""Producers read in their own words.

A producer announcing its own plant, its own investment or its own withdrawal is the strongest
evidence the engine can hold: the publisher and the actor are the same party, so there is nothing to
corroborate (`TIER_PRODUCER_DIRECT` in `producer_signals.verification`). Everything else in the event
lane is a trade publisher reporting on somebody else.

Two channels are read today:

  covestro_press   covestro.com/press/zh-chs/ - Covestro's China newsroom. Low volume and worth it:
                   the June 2026 item announcing a new world-scale MDI train in China is precisely
                   the kind of thing no price desk reports until years later.
  wanhua_news      whchem.com/column/100/ - Wanhua's news centre. Mostly awards and recruitment; it
                   is collected because when Wanhua does speak about a line, it speaks here, and the
                   relevance rules drop the rest without a person having to read it.

Wanhua's capacity decisions reach the market through its exchange filings rather than this page - see
`exchange_filings.py`. This module is the producer's own newsroom, nothing more.

Only the headline, its date, its link and the publisher are kept. No article bodies are stored.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.connectors import index_diagnostics
from app.sources.connectors.event_rules_en import classify_event as classify_en
from app.sources.connectors.event_rules_en import material_of as material_en

# No length cap on the anchor's inner markup. Covestro puts the headline, the timestamp and a summary
# inside one <a>, which a 240-character cap skipped entirely - the first live run found nine article
# links and kept none of them. The cap belongs on the extracted text, not on the HTML around it.
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', re.I | re.S)

# Link text that is furniture rather than a headline. Wanhua lays its newsroom out as cards where the
# headline is a heading and the link underneath reads only "view details", so the anchor text is the
# same seven characters on every item; the headline has to be read from the card around it.
_BOILERPLATE = re.compile(
    r"^(查看详情|了解更多|更多|阅读全文|详情|法律声明|隐私保护|《隐私保护》|read more|learn more|more)\s*[>》]?$", re.I)

# Any date shape this module knows, used to find where a headline ends when the card runs the
# headline and the timestamp together inside one link.
_ANY_DATE_RE = re.compile(
    r"(?:20\d{2}[-/年]\s*\d{1,2}|\d{1,2}/\d{1,2}\.20\d{2}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},)")

# Chinese event vocabulary, matching the categories `producer_signals.export` files them under.
_CN_RULES: tuple[tuple[str, str, str], ...] = (
    ("PLANT_EVENT", "outage", r"停车|检修|故障|停工|事故"),
    ("PLANT_EVENT", "restart", r"开车|复产|重启|投料开车"),
    ("CORPORATE", "capacity_investment", r"投资|增资扩产"),
    ("CORPORATE", "corporate_venture", r"合资|战略合作|战略投资|收购|并购|入股|增资|参股"),
    ("CORPORATE", "corporate_exit", r"退出|剥离|转让股权|出售股权|关停|永久停产"),
    ("CAPACITY", "capacity_addition", r"新建|扩建|扩产|投产|新增产能|装置"),
    ("PRODUCER_PRICE", "producer_price_announcement", r"挂牌价|结算价|价格上调|价格下调"),
)
# Corporate housekeeping a producer's newsroom is full of. None of it is market intelligence.
_CN_SKIP = re.compile(
    r"招聘|虚位以待|职等你来|党建|团委|工会|公益|助学|捐赠|志愿|校园|实习|"
    r"感谢信|致敬|荣获|入选|榜单|认证|奖|展会|亮相|祝|节|运动会")

_DATE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(20\d{2})-(\d{1,2})-(\d{1,2})", "ymd"),
    (r"(20\d{2})/(\d{1,2})/(\d{1,2})", "ymd"),
    # Covestro writes "2026年 7月 13日" - with spaces - so whitespace is allowed between the parts.
    (r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", "ymd"),
    # Wanhua's news centre prints dd/mm.yyyy .
    (r"(\d{1,2})/(\d{1,2})\.(20\d{2})", "dmy"),
    (r"(January|February|March|April|May|June|July|August|September|October|November|December)"
     r"\s+(\d{1,2}),\s*(20\d{2})", "mdy"),
)
_MONTHS = {name: number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"), start=1)}


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", str(text or "")))


def classify_cn(title: str) -> tuple[str, str] | None:
    if _CN_SKIP.search(title):
        return None
    for kind, record_type, pattern in _CN_RULES:
        if re.search(pattern, title):
            return kind, record_type
    return None


def classify(title: str) -> tuple[str, str] | None:
    """Chinese headlines by the Chinese rules, everything else by the shared English vocabulary."""
    return classify_cn(title) if has_chinese(title) else classify_en(title)


_CN_MATERIALS: tuple[tuple[str, str], ...] = (
    ("TDI", "TDI"), ("聚合MDI", "PMDI"), ("纯MDI", "PURE_MDI"), ("MDI", "PMDI"),
    ("聚醚多元醇", "POLYOL"), ("聚醚", "POLYOL"), ("多元醇", "POLYOL"),
    ("环氧丙烷", "PO"), ("环氧乙烷", "EO"), ("甲苯", "TOLUENE"), ("苯胺", "ANILINE"),
    ("液氯", "LIQUID_CHLORINE"), ("硝酸", "NITRIC_ACID"), ("TPU", "TPU"), ("聚氨酯", "POLYURETHANE"),
)


def material_of(title: str) -> str | None:
    if has_chinese(title):
        for label, family in _CN_MATERIALS:
            if label in title:
                return family
        return None
    return material_en(title)


def _text_of(markup: str) -> str:
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))).strip()


def headline_of(anchor_markup: str) -> str:
    """The headline in a link, with the timestamp and the summary that follow it cut away.

    Covestro's card reads "<headline> 2026年 7月 13日 | 10:05 Europe/Amsterdam <two bullet points> 更多"
    as one string. Everything from the date onwards is furniture, and leaving it in puts a paragraph
    of the publisher's summary text on the record where a headline belongs.
    """
    text = _text_of(anchor_markup)
    cut = _ANY_DATE_RE.search(text)
    if cut and cut.start() > 6:
        text = text[: cut.start()].strip(" |-–—·,")
    return text[:240].strip()


def headline_before(html: str, position: int) -> str:
    """The card's own heading, read backwards from a link whose text is only "view details".

    Read back a bounded distance and prefer a real heading element, because that is what a newsroom
    card puts the headline in. Fall back to other text only if the card uses none. A candidate that
    is just the card's date is skipped - taking the nearest text blindly returns the timestamp, which
    is the mistake the first version made.
    """
    window = _card_window(html, position)
    found = re.findall(r"<(h[1-6]|p|span|div|strong|b)\b[^>]*>(?P<t>[^<]{6,240})</\1>", window, re.I)
    headings = [(tag, text) for tag, text in found if tag.lower().startswith("h")]
    for tag, text in reversed(headings or found):
        candidate = _text_of(text)
        if not candidate or len(candidate) < 6 or _BOILERPLATE.match(candidate):
            continue
        if _is_only_a_date(candidate):
            continue
        return candidate[:240]
    return ""


def _is_only_a_date(text: str) -> bool:
    """True when the text carries a date and nothing else worth calling a headline."""
    stripped = _ANY_DATE_RE.sub(" ", text)
    stripped = re.sub(r"[\d\s:./年月日|,-]+", "", stripped)
    return len(stripped) < 3


def _card_window(html: str, position: int) -> str:
    """The markup between the previous link and this one - one card, and not its neighbour's."""
    window = html[max(0, position - 2000):position]
    previous = window.rfind("</a>")
    return window[previous + 4:] if previous >= 0 else window


def date_before(html: str, position: int) -> datetime | None:
    """The date printed above a link, for cards that put the timestamp before the "view details"."""
    return _first_date(_text_of(_card_window(html, position)))


def date_in(markup: str) -> datetime | None:
    """A date printed inside the link itself, which is where Covestro puts it."""
    return _first_date(_text_of(markup))


def date_near(html: str, position: int) -> datetime | None:
    """The date printed beside an item, read forward from its link and stopped at the next one.

    A newsroom card puts the date after the headline. Stopping at the next anchor keeps one item from
    inheriting the date below it; an item whose date cannot be seen is left undated rather than
    stamped with a guess, because a wrong date on a signal is worse than no date.
    """
    window = html[position:position + 400]
    next_anchor = window.find("<a ")
    if next_anchor > 0:
        window = window[:next_anchor]
    return _first_date(_text_of(window))


def _first_date(text: str) -> datetime | None:
    for pattern, order in _DATE_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if order == "ymd":
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif order == "dmy":
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            else:
                month = _MONTHS[match.group(1)]
                day, year = int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
    return None


def parse_index_events(html: str, *, article_re: re.Pattern[str], base_url: str) -> list[dict]:
    """Event headlines linked from a producer's newsroom index."""
    out: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(html):
        href = html_lib.unescape(match.group("href"))
        if not article_re.search(href):
            continue
        anchor_markup = match.group("title")
        # A picture link: <a class="img" href="..."><img ...></a>. It carries no text of its own, and
        # reading backwards from it lands in the card ABOVE, so it would borrow that card's headline.
        # The same card's "view details" link is read a few lines later and carries it properly.
        if not _text_of(anchor_markup) and "<img" in anchor_markup.lower():
            continue
        headline = headline_of(anchor_markup)
        from_card = False
        if not headline or _BOILERPLATE.match(headline):
            # A "view details" link: the headline lives in the card above it, and so does the date.
            headline = headline_before(html, match.start())
            from_card = True
        if len(headline) < 6 or _BOILERPLATE.match(headline) or headline in seen:
            continue
        classified = classify(headline)
        if classified is None:
            continue
        seen.add(headline)
        kind, record_type = classified
        out.append({
            "headline": headline,
            "kind": kind,
            "record_type": record_type,
            "material": material_of(headline),
            # The date is inside the link on some cards and beside it on others, so both are read.
            # The date sits inside the link on some cards, above it on "view details" cards, and
            # beside it on others. Reading forward on a card layout picks up the NEXT card's date.
            "event_date": (date_before(html, match.start()) if from_card
                           else date_in(anchor_markup) or date_near(html, match.end())),
            "url": href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/"),
        })
    return out


class ProducerPressConnector:
    """One producer's own newsroom, read as an event lane."""

    allow_empty_parse = True

    def __init__(self, *, source_id: str, source_url: str, base_url: str,
                 publisher: str, article_pattern: str, geography: str = "China"):
        self.source_id = source_id
        self.source_url = source_url
        self.base_url = base_url
        self.publisher = publisher
        self.geography = geography
        self._article_re = re.compile(article_pattern, re.I)

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        response = httpx.get(
            item.source_url, follow_redirects=True, timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        response.raise_for_status()
        return response.content

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        events = parse_index_events(html, article_re=self._article_re, base_url=self.base_url)
        index_diagnostics.write(self.source_id, src, html,
                                is_article=lambda href: bool(self._article_re.search(href or "")),
                                classify=classify, kept=events)
        return [self._event_record(src, event) for event in events]

    def extract(self, item: SourceItem, content: bytes) -> list[ExtractedRecord]:
        return self.extract_html(content.decode("utf-8", errors="ignore"), item.source_url)

    def _event_record(self, src: str, event: dict) -> ExtractedRecord:
        return ExtractedRecord(
            kind="event",
            source_url=src,
            publication_date=event["event_date"],
            data={
                "type": event["record_type"],
                "event_type": event["kind"],
                "headline": event["headline"],
                "material": event["material"],
                "product": event["material"],
                "market_geography": self.geography,
                "publisher": self.publisher,
                # The producer is the publisher, so the actor is known without reading the headline.
                "actor": self.publisher,
                "url": event["url"],
                "observation_date": event["event_date"],
            },
        )


def covestro_press() -> ProducerPressConnector:
    return ProducerPressConnector(
        source_id="covestro_press",
        source_url="https://www.covestro.com/press/zh-chs/",
        base_url="https://www.covestro.com",
        publisher="Covestro",
        article_pattern=r"/press/zh-chs/[a-z0-9-]{8,}/",
    )


def wanhua_news() -> ProducerPressConnector:
    return ProducerPressConnector(
        source_id="wanhua_news",
        source_url="https://www.whchem.com/column/100/",
        base_url="https://www.whchem.com",
        publisher="Wanhua Chemical",
        article_pattern=r"/cmscontent/\d+\.html",
    )
