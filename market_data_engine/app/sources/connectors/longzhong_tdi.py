from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timezone

import requests
from urllib.parse import urljoin

from app.domain.schemas import ExtractedRecord, SourceItem
from app.sources.connectors.longzhong_plant_table import (
    is_plant_table_bulletin,
    is_utilisation_bulletin,
    parse_plant_table,
    parse_utilisation_bulletin,
)
from app.sources.connectors.mysteel_pu import PU_PRICE_PAGE, parse_pu_events, parse_pu_price_block
from raw_material_forecasting import price_kinds


class LongzhongTDIConnector:
    source_id = "longzhong_tdi"
    source_url = "https://list.oilchem.net/sem/392/7.html"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        }

    history_listing_url = "https://list.oilchem.net/392/6581/"

    # Mysteel's polyurethane landing page is the same publisher (Mysteel owns Longzhong; the
    # articles are bylined 隆众资讯) but a better page: it is a day fresher than the
    # list.oilchem.net table and is server-rendered. Both are collected under this one
    # source_id so the independent-source count stays honest.
    mysteel_pu_url = PU_PRICE_PAGE

    # How many statistics bulletins of either kind one run will fetch. Each appears once per
    # chemical per week, so the page carries a handful at a time; the cap is here so a change in
    # the publisher's markup can never turn one run into a hundred requests.
    #
    # Six was the count of a gate that read two desks' wording. MEASURED over the 65 landing pages
    # archived between 9 September and 18 November 2026, the page names 7 to 9 of these bulletins
    # and never more: at six, a third of each day's postings was cut off silently, and which third
    # depended on the publisher's running order. Twelve clears the measured ceiling and still
    # bounds a markup change to one page's worth of requests.
    max_plant_bulletins = 12

    def discover(self) -> list[SourceItem]:
        items = [SourceItem(source_url=self.source_url), SourceItem(source_url=self.mysteel_pu_url)]
        # The plant bulletins are found by reading the page we are about to collect anyway. A
        # failure here must never fail discovery: the runner treats a discovery error as a dead
        # run, and the two pages above are the feed. Worst case the bulletins wait for tomorrow.
        try:
            payload = self._request(self.mysteel_pu_url)
            # The runner fetches this same page again as the second item a moment later. Handing
            # that one fetch back saves the publisher a daily request; it is kept for one
            # collection only, and `fetch_item` falls through to the network if it is not there.
            self._discovered_payload = (self.mysteel_pu_url, payload)
            items.extend(self.discover_bulletins(payload.decode("utf-8", errors="replace")))
        except Exception:
            self._discovered_payload = None
        return items

    def discover_bulletins(self, html: str) -> list[SourceItem]:
        """The weekly statistics bulletins linked from the polyurethane page.

        Two postings carry a readable body: the maintenance table (which plants, at what capacity,
        down since when) and the capacity-utilisation summary (one operating rate for the whole
        chemical). A one-plant news item carries prose and stays with the headline stream.

        The maintenance bulletin is published on a Friday and has rotated off the landing page by
        Monday - 17 November found none - so this returns what is there today, and a daily run is
        what catches each one. `mysteel_pu` says the same thing about the intraday items.
        """
        seen: set[str] = set()
        out: list[SourceItem] = []
        for event in parse_pu_events(html):
            url = event["url"]
            headline = event["headline"]
            if url in seen or not (is_plant_table_bulletin(headline) or is_utilisation_bulletin(headline)):
                continue
            seen.add(url)
            out.append(SourceItem(source_url=url))
            if len(out) >= self.max_plant_bulletins:
                break
        return out

    def discover_history_html(self, html: str, *, start: date, end: date) -> list[SourceItem]:
        found: list[str] = []
        pattern = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*TDI[^<]*)</a>\s*\[(20\d{2}-\d{2}-\d{2})', re.I)
        for href, _title, date_text in pattern.findall(html):
            dt = datetime.strptime(date_text, "%Y-%m-%d").date()
            if start <= dt <= end:
                url = urljoin("https://list.oilchem.net", href)
                if url not in found:
                    found.append(url)
        return [SourceItem(source_url=url) for url in found]

    def historical_listing_items(self, pages: int = 100) -> list[SourceItem]:
        return [SourceItem(source_url=f"{self.history_listing_url}{page}.html") for page in range(1, pages + 1)]

    def _request(self, url: str) -> bytes:
        session = requests.Session()
        session.headers.update(self._headers())
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            direct = requests.Session()
            direct.trust_env = False
            direct.headers.update(self._headers())
            response = direct.get(url, timeout=30)
            response.raise_for_status()
            return response.content

    _discovered_payload: tuple[str, bytes] | None = None

    def fetch_item(self, item: SourceItem) -> bytes:
        held = self._discovered_payload
        if held is not None and held[0] == item.source_url:
            self._discovered_payload = None     # one collection only; never a cache between runs
            return held[1]
        return self._request(item.source_url)

    def fetch(self) -> bytes:
        return self._request(self.source_url)

    def _record(self, src: str, price: float, dt: datetime) -> ExtractedRecord:
        return ExtractedRecord(
            kind="price",
            source_url=src,
            publication_date=dt,
            data={
                "type": "price",
                "product": "TDI",
                "market_geography": "China",
                "commercial_basis": "EXW",
                "price": price,
                "currency": "CNY",
                "unit": "t",
                "observation_type": price_kinds.ASSESSED,
                "observation_date": dt,
            },
        )

    def _event_records(self, html: str, src: str) -> list[ExtractedRecord]:
        """Headline-only market-intelligence events. No body text is stored - title, date, link, publisher."""
        out: list[ExtractedRecord] = []
        for event in parse_pu_events(html):
            out.append(ExtractedRecord(
                kind="event",
                # Must be the URL that was fetched: the runner keys stored evidence by it and
                # silently drops records whose source_url does not match. The article link lives
                # in data["url"].
                source_url=src,
                publication_date=event["event_date"],
                data={
                    "type": event["record_type"],
                    "event_type": event["kind"],
                    "headline": event["headline"],
                    "material": event["material"],
                    "product": event["material"],
                    "market_geography": "China",
                    "publisher": "Longzhong / Mysteel",
                    "url": event["url"],
                    "observation_date": event["event_date"],
                },
            ))
        return out

    _ARTICLE_URL = re.compile(r"/oilchem/a/\d{8}/", re.I)

    def _bulletin_records(self, html: str, src: str) -> list[ExtractedRecord]:
        """One event per plant row in a weekly maintenance bulletin.

        The headline stream already files the bulletin itself; these are a different grain - the
        plants inside it, with capacity, load and outage dates - and they hang off the article's
        own evidence rather than the listing page's, because that article IS the document they
        were read from.

        A bulletin whose table cannot be read still returns one record. The runner counts an item
        that parsed to nothing as an error and drops the whole run to PARTIAL, and a feed that
        delivered its prices must not read as broken because a publisher changed a table.
        """
        title = ""
        heading = re.search(r"<title>\s*(?P<t>[^<]+?)\s*</title>", html, re.I)
        if heading:
            title = html_lib.unescape(heading.group("t")).split("_")[0].strip()

        # The utilisation bulletin is one number for a whole chemical and sits ahead of the
        # publisher's cut, so it is read first and on its own terms.
        if is_utilisation_bulletin(title):
            summary = parse_utilisation_bulletin(html, headline=title)
            observed = datetime.now(timezone.utc)
            if summary is None:
                return [ExtractedRecord(
                    kind="event", source_url=src, publication_date=observed,
                    data={"type": "plant_table_unreadable", "event_type": "CAPACITY",
                          "material": None, "product": None, "market_geography": "China",
                          "publisher": "Longzhong / Mysteel", "url": src,
                          "observation_date": observed,
                          "headline": title or "capacity utilisation bulletin"},
                )]
            # What this record IS: one chemical's rate for one week. The store dedups features on
            # it, because the article page carries a rolling sidebar and so hashes differently on
            # every fetch - without a key of its own, a daily run files the same weekly rate again
            # every day. A bulletin with no readable week falls back to the hash and may repeat;
            # that is the lesser fault, and it is visible.
            key = (f"{summary['material']}:{summary['period']}"
                   if summary.get("material") and summary.get("period") else None)
            return [ExtractedRecord(
                kind="event", source_url=src, publication_date=observed,
                data={"type": "capacity_utilisation", "event_type": "CAPACITY",
                      "material": summary["material"], "product": summary["material"],
                      "market_geography": "China", "publisher": "Longzhong / Mysteel",
                      "url": src, "observation_date": observed, "headline": title,
                      # `feature_observation.value` is the numeric column anything reading these
                      # looks at, and it is filled from `value` alone. Without this the rate sits
                      # in the JSON and the column is null, which is how every capacity_utilisation
                      # row in the store came to carry no number at all (measured, 17 November).
                      "value": summary["operating_rate"],
                      **({"observation_key": key} if key else {}),
                      **{k: v for k, v in summary.items() if k != "material"}},
            )]

        table = parse_plant_table(html, headline=title)
        observed = (datetime.fromisoformat(table["table_date"]) if table["table_date"]
                    else datetime.now(timezone.utc))
        common = {
            "event_type": "PLANT_EVENT",
            "material": table["material"],
            "product": table["material"],
            "market_geography": "China",
            "publisher": "Longzhong / Mysteel",
            "url": src,
            "observation_date": observed,
            # Every bulletin body this publisher serves is cut after a few rows. A plant missing
            # from `rows` is a plant we were not shown, never a plant running normally, and
            # anything reading these records has to be able to tell the difference.
            "table_truncated": table["truncated"],
        }
        if not table["rows"]:
            return [ExtractedRecord(
                kind="event", source_url=src, publication_date=observed,
                data={**common, "type": "plant_table_unreadable",
                      "headline": title or "plant maintenance bulletin"},
            )]

        out: list[ExtractedRecord] = []
        for row in table["rows"]:
            out.append(ExtractedRecord(
                kind="event", source_url=src, publication_date=observed,
                data={**common,
                      "type": "plant_status",
                      "headline": f"{row['company']}（{row['capacity_kt_per_year']:g} kt/y）{row['status']}",
                      "company": row["company"],
                      "region": row["region"],
                      "capacity_kt_per_year": row["capacity_kt_per_year"],
                      "capacity_wan_t_per_year": row["capacity_wan_t_per_year"],
                      "load_fraction": row["load_fraction"],
                      "outage": row["outage"],
                      "status_text": row["status"]},
            ))
        return out

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url

        # A bulletin article, discovered from the polyurethane page. Its body is the plant table;
        # it carries neither the price block nor the headline stream, so it is read on its own.
        if self._ARTICLE_URL.search(src):
            return self._bulletin_records(html, src)

        # Mysteel polyurethane landing page: one dated TDI price in a server-rendered block, plus the
        # free producer-price / plant-event / fundamentals headline streams (see mysteel_pu.py).
        # The two are read independently: if the publisher ever drops or masks the price block, the
        # intelligence stream must keep flowing, and vice versa.
        mysteel: list[ExtractedRecord] = [
            self._record(src, row["price"], row["observation_date"])
            for row in parse_pu_price_block(html) if row["product_family"] == "TDI"
        ]
        mysteel.extend(self._event_records(html, src))
        if mysteel:
            return mysteel

        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()
        if "TDI" not in text.upper():
            return []

        # Public 7-day table: date | quote | change | change rate.
        matches = re.findall(
            r"(20\d{2}-\d{2}-\d{2})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:[-+]?[0-9]+(?:\.[0-9]+)?|0)\s+(?:[-+]?[0-9]+(?:\.[0-9]+)?%|0%)",
            text,
        )
        out: list[ExtractedRecord] = []
        seen: set[tuple[str, float]] = set()
        for date_text, price_text in matches:
            key = (date_text, float(price_text))
            if key in seen:
                continue
            seen.add(key)
            dt = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            out.append(
                ExtractedRecord(
                    kind="price",
                    source_url=src,
                    publication_date=dt,
                    data={
                        "type": "price",
                        "product": "TDI",
                        "market_geography": "China",
                        "commercial_basis": "EXW",
                        "price": float(price_text),
                        "currency": "CNY",
                        "unit": "t",
                        "observation_type": price_kinds.ASSESSED,
                        "observation_date": dt,
                    },
                )
            )
        return out
