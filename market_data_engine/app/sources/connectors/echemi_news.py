"""Market-intelligence events from ECHEMI's free English chemical news.

Why this publisher. The engine had two event publishers, Longzhong (which Mysteel also fronts) and
SunSirs, and both write a price desk's news: what a plant did today and what a price did today.
Almost nothing they carry answers the questions a company that buys TDI asks on a longer horizon -
who is building capacity, who is putting money in, who is coming into the market or leaving it.

ECHEMI publishes exactly that, in a section it calls Company Dynamic, alongside policy and trade
items that move isocyanate economics before any price prints. It is a third independent lineage, so
it also gives Longzhong and SunSirs something to be corroborated against that is not each other.

Scope is deliberately the whole chemical board, not TDI alone. A PDH unit or a methanol outage is not
a TDI story, and the relevance graph in `producer_signals.relevance` is what decides whether a reader
ever sees it - filtering here on the word "TDI" would throw the chain away before that graph runs.

Only the headline, its date, its link and the publisher are kept. Article bodies are ECHEMI's
copyrighted text and are not stored.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.connectors import index_diagnostics
from app.sources.connectors.event_rules_en import classify_event, material_of

# ECHEMI article pages are /cms/<digits>.html . Anything else on the index is navigation.
_ARTICLE_HREF_RE = re.compile(r"/cms/(?P<id>\d{4,})\.html", re.I)
# No length cap on the anchor's inner markup. The first version capped it at 240 characters, which
# silently skipped every link whose card carries a heading, a date and a summary inside the <a> -
# and that is most of them. The cap belongs on the extracted TEXT, not on the HTML around it.
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', re.I | re.S)
# The index prints each item's date in a <span class="date"> right after its link. The live page
# writes it as "Sep 10, 2026"; an ISO form is accepted too, because a site that renders one today can
# render the other tomorrow and a parser that knows only one silently stamps everything with today.
_ISO_DATE_RE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
_SHORT_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(20\d{2})", re.I)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def is_article_url(url: str) -> bool:
    return bool(_ARTICLE_HREF_RE.search(url or ""))


def absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return "https://www.echemi.com/" + href.lstrip("/")


def clean_headline(raw: str) -> str:
    """Anchor text with markup stripped. ECHEMI wraps some headlines in <span>/<h*> elements."""
    text = re.sub(r"<[^>]+>", " ", raw)
    return html_lib.unescape(re.sub(r"\s+", " ", text)).strip()


def _date_near(html: str, position: int) -> datetime | None:
    """The date ECHEMI prints beside an item, taken from the markup that follows its link.

    Read forward from the anchor and stop at the next anchor, so one item can never borrow the date
    of the one below it. A window of a few hundred characters covers the card markup; beyond that we
    would be guessing, and an item with no date we can see is better left undated than dated wrong.
    """
    window = html[position:position + 400]
    next_anchor = window.find("<a ")
    if next_anchor > 0:
        window = window[:next_anchor]
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", window))
    return parse_date(text)


def parse_date(text: str) -> datetime | None:
    """"Sep 10, 2026" or "2026-09-10". Anything else is left undated rather than guessed at."""
    match = _SHORT_DATE_RE.search(text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        if month:
            try:
                return datetime(int(match.group(3)), month, int(match.group(2)), tzinfo=timezone.utc)
            except ValueError:
                return None
    match = _ISO_DATE_RE.search(text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def parse_index_events(html: str) -> list[dict]:
    """Event headlines linked from an ECHEMI news index.

    Returns headline / kind / record_type / material / event_date / url. Commentary and the
    recurring sourcing-request and import-analysis round-ups are dropped by the shared skip rules.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(html):
        href = html_lib.unescape(match.group("href"))
        if not is_article_url(href):
            continue
        headline = clean_headline(match.group("title"))
        if len(headline) < 12 or headline in seen:
            continue
        classified = classify_event(headline)
        if classified is None:
            continue
        seen.add(headline)
        kind, record_type = classified
        out.append({
            "headline": headline,
            "kind": kind,
            "record_type": record_type,
            "material": material_of(headline),
            "event_date": _date_near(html, match.end()),
            "url": absolute(href),
        })
    return out


class EchemiNewsConnector:
    """ECHEMI's news index, read as an event lane only. No prices are taken from this page."""

    allow_empty_parse = True
    source_id = "echemi_news"
    source_url = "https://www.echemi.com/cms-news.html"

    def discover(self) -> list[SourceItem]:
        # The index alone. Article pages are not fetched: the headline carries the event, and the
        # body is the publisher's copyrighted prose, so there is nothing there we are willing to keep.
        return [SourceItem(source_url=self.source_url)]

    # The first live run proved the plain fetch is useless here: 110 KB of HTML containing ZERO
    # anchors. ECHEMI builds the news list in the browser, so the server sends a shell. The index is
    # therefore read the way the engine already reads ECHEMI's price pages - a real off-screen Edge
    # rendering the page, and only reading what it rendered (see edge_cdp.py).
    _READY_MARKER = "/cms/"

    def _fetch_rendered_edge(self, item: SourceItem) -> bytes:
        from app.sources.edge_cdp import fetch_rendered_html

        markup = fetch_rendered_html(item.source_url, contains=(self._READY_MARKER,))
        return markup.encode("utf-8")

    def _fetch_http(self, item: SourceItem) -> bytes:
        response = httpx.get(
            item.source_url, follow_redirects=True, timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                     "Accept-Language": "en-US,en;q=0.9"})
        response.raise_for_status()
        return response.content

    def fetch_item(self, item: SourceItem) -> bytes:
        from app.sources.edge_cdp import EdgeUnavailable

        try:
            return self._fetch_rendered_edge(item)
        except EdgeUnavailable:
            # No Edge on this machine: the plain fetch will find nothing, and the diagnostics file
            # says so plainly rather than the run looking like a quiet news day.
            return self._fetch_http(item)

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        events = parse_index_events(html)
        index_diagnostics.write(self.source_id, src, html,
                                is_article=is_article_url, classify=classify_event, kept=events)
        return [self._event_record(src, event) for event in events]

    def extract(self, item: SourceItem, content: bytes) -> list[ExtractedRecord]:
        return self.extract_html(content.decode("utf-8", errors="ignore"), item.source_url)

    def _event_record(self, src: str, event: dict) -> ExtractedRecord:
        return ExtractedRecord(
            kind="event",
            # The URL that was fetched: the runner keys stored evidence by it and drops records whose
            # source_url does not match. The article's own link is carried in data["url"].
            source_url=src,
            publication_date=event["event_date"],
            data={
                "type": event["record_type"],
                "event_type": event["kind"],
                "headline": event["headline"],
                "material": event["material"],
                "product": event["material"],
                # ECHEMI reports globally. Geography is left unstated rather than assumed to be China,
                # which is what the other two publishers can be assumed to mean and this one cannot.
                "market_geography": "",
                "publisher": "ECHEMI",
                "url": event["url"],
                "observation_date": event["event_date"],
            },
        )
