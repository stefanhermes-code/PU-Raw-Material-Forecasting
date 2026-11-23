from __future__ import annotations

import html as html_lib
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import requests

from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.connectors import sunsirs_events
from raw_material_forecasting import price_kinds

_RAW_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


class SunSirsTDIConnector:
    source_id = "sunsirs_tdi"
    source_url = "https://www.sunsirs.com/uk/prodetail-1095.html"
    daily_spot_url = source_url
    news_url = "https://www.sunsirs.com/uk/news-635-1.html"
    base_url = "https://www.sunsirs.com"
    # The weekly snapshot pages are out of the routine backfill. Between 25 September and 17 October
    # 2026 the daily backfill fetched 151 of them and parsed 0 readings - every fetch a different
    # page, which is an interstitial, not a price table - and reported the source "changed" on every
    # run after the first of the day. `historical_weekly_items` stays for a deliberate manual run.
    routine_backfill = False

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

    def _request(self, url: str) -> bytes:
        session = requests.Session()
        session.headers.update(self._headers())
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            # Windows/Python can inherit a proxy path that differs from the browser.
            # Retry directly before marking the public SunSirs source failed.
            direct = requests.Session()
            direct.trust_env = False
            direct.headers.update(self._headers())
            response = direct.get(url, timeout=30)
            response.raise_for_status()
            return response.content

    def discover(self) -> list[SourceItem]:
        """The daily spot page, plus the TDI news articles that carry the event lane.

        The price lane must stay fast and deterministic, so a news index that is slow or down can
        never hold prices up: discovery of the articles is best-effort and its failure is swallowed.
        The articles matter because SunSirs is our only *independent* event publisher - without them
        no signal can ever be corroborated and every anonymous report is discarded unverified.
        """
        items = [SourceItem(source_url=self.daily_spot_url)]
        try:
            index_html = self._request(self.news_url).decode("utf-8", errors="ignore")
        except Exception:
            return items

        # The index is a table of contents, not a document: it is used to find articles and is NOT
        # itself collected. Fetching it made the runner demand records from a page that has none,
        # which reported the whole source PARTIAL with "no records parsed" on a perfectly good run.
        articles = self.discover_html(index_html)
        seen = {item.source_url for item in items}
        for article in articles:
            if article.source_url not in seen:
                seen.add(article.source_url)
                items.append(article)
        self._record_event_lane_diagnostics(index_html, articles)
        return items

    def _record_event_lane_diagnostics(self, index_html: str, articles: list[SourceItem]) -> None:
        """A bounded snapshot of what the news index actually offered on this run.

        The index cannot be read from outside the machine that collects it - SunSirs answers a
        cloud IP with a security interstitial - so without this the event lane can only be tuned by
        guessing at markup, which is how false negatives get written into a parser. Headlines and
        links only; no article text, and never more than a page's worth. Failure here is swallowed:
        a diagnostic must not be able to break a collection.
        """
        try:
            from app.producer_signals.export import default_feed_path

            anchors = _RAW_ANCHOR_RE.findall(index_html)
            candidates = []
            for href, anchor_html in anchors[:400]:
                title = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", anchor_html))).strip()
                if not sunsirs_events.is_article_url(href) or not title:
                    continue
                classified = sunsirs_events.classify_event(sunsirs_events.clean_headline(title))
                candidates.append({
                    "href": href,
                    "title": title[:180],
                    "classified_as": classified[0] if classified else None,
                })
            payload = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "index_url": self.news_url,
                "bytes": len(index_html),
                "anchors_total": len(anchors),
                "article_links": len(candidates),
                "tdi_articles_discovered": [item.source_url for item in articles][:40],
                "candidates": candidates[:60],
            }
            path = default_feed_path().parent / "event_lane_diagnostics.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            return

    def discover_html(self, index_html: str) -> list[SourceItem]:
        found: list[str] = []
        anchor_re = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
        for href, anchor_html in anchor_re.findall(index_html):
            if not re.search(r"(?:detail_news-|commodity-news/petail-)", href, re.I):
                continue
            title = re.sub(r"<[^>]+>", " ", anchor_html)
            title = html_lib.unescape(re.sub(r"\s+", " ", title)).strip()
            if not re.search(r"\bTDI\b", title, re.I):
                continue
            url = urljoin(self.base_url, href)
            if url not in found:
                found.append(url)
        return [SourceItem(source_url=url) for url in found]


    def historical_weekly_items(self, start: date, end: date) -> list[SourceItem]:
        """Generate SunSirs weekly snapshot URLs for backfill without search-engine dependence."""
        if end < start:
            return []
        current = start
        items: list[SourceItem] = []
        while current <= end:
            items.append(SourceItem(source_url=f"https://www.sunsirs.com/uk/sdetail-week-{current:%Y-%m%d}.html"))
            current += timedelta(days=7)
        return items

    @staticmethod
    def _weekly_snapshot_date(source_url: str) -> datetime | None:
        match = re.search(r"sdetail-week-(20\d{2})-(\d{2})(\d{2})\.html", source_url)
        if not match:
            return None
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc) + timedelta(days=4)
        except ValueError:
            return None

    def fetch_item(self, item: SourceItem) -> bytes:
        return self._request(item.source_url)

    def fetch(self) -> bytes:
        return self._request(self.source_url)

    @staticmethod
    def _publication_date(text: str) -> datetime | None:
        patterns = [
            (r"\b([A-Z][a-z]+\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\b", "%B %d %Y %H:%M:%S"),
            (r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b", "%Y-%m-%d %H:%M:%S"),
            (r"\b(\d{4}-\d{2}-\d{2})\b", "%Y-%m-%d"),
        ]
        for pattern, fmt in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return datetime.strptime(match.group(1), fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        return None

    @staticmethod
    def _observation_date(text: str, publication_date: datetime | None) -> datetime | None:
        match = re.search(r"\bAs of\s+([A-Z][a-z]+)\s+(\d{1,2})\b", text, re.I)
        if not match or publication_date is None:
            return None
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)} {publication_date.year}", "%B %d %Y"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _event_record(self, src: str, event: dict, published: datetime | None) -> ExtractedRecord:
        """Headline-only event. The article body is SunSirs' copyrighted prose and is not stored."""
        return ExtractedRecord(
            kind="event",
            # Must be the URL that was fetched: the runner keys stored evidence by it and silently
            # drops records whose source_url does not match. The article link lives in data["url"].
            source_url=src,
            publication_date=published,
            data={
                "type": event["record_type"],
                "event_type": event["kind"],
                "headline": event["headline"],
                "material": event["material"],
                "product": event["material"],
                "market_geography": "China",
                "publisher": "SunSirs",
                "url": event["url"],
                "observation_date": published,
            },
        )

    def _index_event_records(self, html: str, src: str) -> list[ExtractedRecord]:
        published = self._publication_date(html_lib.unescape(re.sub(r"<[^>]+>", " ", html)))
        return [self._event_record(src, event, published)
                for event in sunsirs_events.parse_index_events(html)]

    def _article_event_records(self, html: str, src: str) -> list[ExtractedRecord]:
        title = sunsirs_events.article_title(html)
        classified = sunsirs_events.classify_event(title) if title else None
        if classified is None:
            return []                      # commentary, not an event: fall through to the price lane
        kind, record_type = classified
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        event = {"headline": title, "kind": kind, "record_type": record_type,
                 "material": sunsirs_events.material_of(title), "url": src}
        return [self._event_record(src, event, self._publication_date(re.sub(r"\s+", " ", text)))]

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()

        if src == self.news_url:
            return self._index_event_records(html, src)

        if sunsirs_events.is_article_url(src):
            events = self._article_event_records(html, src)
            if events:
                return events

        if src == self.daily_spot_url or "China TDI Spot Price" in text or "Spot Price: Daily" in text:
            daily_rows = re.findall(
                r"\bTDI\b\s+Chemical\s+([0-9,]+(?:\.[0-9]+)?)\s+(20\d{2}-\d{2}-\d{2})\b",
                text,
                re.I,
            )
            if daily_rows:
                records: list[ExtractedRecord] = []
                for price_text, date_text in daily_rows:
                    observation = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    data = {
                        "type": "price",
                        "product": "TDI",
                        "market_geography": "China",
                        "commercial_basis": "EXW",
                        "price": float(price_text.replace(",", "")),
                        "currency": "CNY",
                        "unit": "t",
                        "observation_type": price_kinds.ASSESSED,
                        "observation_date": observation,
                    }
                    records.append(
                        ExtractedRecord(
                            kind="price",
                            source_url=src,
                            publication_date=observation,
                            data=data,
                        )
                    )
                return records

            # Legacy generic daily-table fallback retained for older SunSirs snapshots.
            date_match = re.search(r"Price Chart\s*-\s*(\d{2}/\d{2}/\d{4})", text, re.I)
            daily_match = re.search(
                r"\bTDI\b\s*(?:\|\s*)?Chemical\s*(?:\|\s*)?"
                r"([0-9,]+(?:\.[0-9]+)?)\s*(?:\|\s*)?"
                r"([0-9,]+(?:\.[0-9]+)?)\s*(?:\|\s*)?[-+0-9.]+%",
                text,
                re.I,
            )
            if date_match and daily_match:
                observation = datetime.strptime(date_match.group(1), "%d/%m/%Y").replace(tzinfo=timezone.utc)
                price = float(daily_match.group(2).replace(",", ""))
                data = {
                    "type": "price",
                    "product": "TDI",
                    "market_geography": "China",
                    "commercial_basis": "EXW",
                    "price": price,
                    "currency": "CNY",
                    "unit": "t",
                    "observation_type": price_kinds.ASSESSED,
                    "observation_date": observation,
                }
                return [
                    ExtractedRecord(
                        kind="price",
                        source_url=src,
                        publication_date=observation,
                        data=data,
                    )
                ]

        publication = self._publication_date(text)
        observation = self._observation_date(text, publication)

        price_match = re.search(
            r"average market price in East China\s+(?:stood at|was)\s*([0-9,]+(?:\.[0-9]+)?)\s*(?:RMB|yuan)\s*/\s*ton",
            text,
            re.I,
        )
        if not price_match:
            price_match = re.search(
                r"East China.*?average market price.*?([0-9,]+(?:\.[0-9]+)?)\s*(?:RMB|yuan)\s*/\s*ton",
                text,
                re.I,
            )
        if not price_match:
            # Weekly snapshot pages expose start/end values in a compact commodity row.
            weekly = re.search(
                r"\bTDI\b\s+([0-9,]+(?:\.[0-9]+)?)\s+([0-9,]+(?:\.[0-9]+)?)\s+[-+0-9.]+%",
                text, re.I,
            )
            if not weekly:
                return []
            publication = publication or self._weekly_snapshot_date(src)
            observation = publication
            price_match = weekly
            weekly_price = float(weekly.group(2).replace(",", ""))
        else:
            weekly_price = float(price_match.group(1).replace(",", ""))

        data = {
            "type": "price",
            "product": "TDI",
            "market_geography": "China" if "sdetail-week-" in src else "East China",
            "commercial_basis": "EXW",
            "price": weekly_price,
            "currency": "CNY",
            "unit": "t",
            "observation_type": price_kinds.ASSESSED,
            "observation_date": observation,
        }
        return [ExtractedRecord(kind="price", source_url=src, publication_date=publication, data=data)]
