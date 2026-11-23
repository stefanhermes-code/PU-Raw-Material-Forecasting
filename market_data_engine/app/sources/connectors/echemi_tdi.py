from __future__ import annotations

import base64
import html as html_lib
import re
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds


class EchemiTDIConnector:
    source_id = "echemi_tdi"
    source_url = (
        "https://www.echemi.com/productsInformation/"
        "pd20170215195139318-toluene-diisocyanate.html"
    )
    weekly_fallback_url = "https://www.echemi.com/weekly-price-list.html"
    china_fob_curve_url = (
        "https://www.echemi.com/price-curve/"
        "china-fob-pd20170215195139318-2.html"
    )
    europe_curve_url = (
        "https://www.echemi.com/price-curve/"
        "europe-cfr-pd20170215195139318-2.html"
    )
    historical_curve_urls = (
        "https://www.echemi.com/price-curve/shanghai-basf-pd20170215195139318-4.html",
        china_fob_curve_url,
        "https://www.echemi.com/price-curve/east-china-shanghai-pd20170215195139318-3.html",
    )
    _DATE_RE = r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}"
    _TAB_NAMES = (
        "China Domestic Price",
        "International Price",
        "China Regional Price",
        "Enterprise Price",
    )

    def discover(self) -> list[SourceItem]:
        # Two server-rendered curve pages cover the governed TDI market set:
        # the China FOB curve carries China domestic plus the other international
        # markets; the Europe CFR curve supplies the focal China FOB row.
        return [
            SourceItem(source_url=self.china_fob_curve_url),
            SourceItem(source_url=self.europe_curve_url),
        ]

    def historical_items(self) -> list[SourceItem]:
        return [SourceItem(source_url=url) for url in self.historical_curve_urls]

    @staticmethod
    def _request_headers() -> dict[str, str]:
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

    def _fetch_http(self, item: SourceItem) -> bytes:
        req = Request(item.source_url, headers=self._request_headers())
        try:
            with urlopen(req, timeout=30) as response:
                return response.read()
        except URLError:
            opener = build_opener(ProxyHandler({}))
            with opener.open(req, timeout=30) as response:
                return response.read()

    @staticmethod
    def _build_rendered_bundle(captures: list[tuple[str, str, bytes]]) -> bytes:
        sections: list[str] = []
        for tab_name, visible_text, screenshot in captures:
            encoded = base64.b64encode(screenshot).decode("ascii")
            sections.append(
                '<section data-echemi-tab="{}">'
                '<pre>{}</pre>'
                '<img data-evidence-screenshot="{}" src="data:image/png;base64,{}" />'
                "</section>".format(
                    html_lib.escape(tab_name, quote=True),
                    html_lib.escape(visible_text),
                    html_lib.escape(tab_name, quote=True),
                    encoded,
                )
            )
        return (
            '<html><body data-acquisition="browser-rendered">'
            + "".join(sections)
            + "</body></html>"
        ).encode("utf-8")

    @staticmethod
    def _launch_system_browser(playwright):
        errors: list[str] = []
        for label, kwargs in (
            ("msedge", {"channel": "msedge"}),
            ("chrome", {"channel": "chrome"}),
            ("chromium", {}),
        ):
            try:
                return playwright.chromium.launch(headless=True, **kwargs)
            except Exception as exc:  # pragma: no cover - environment dependent
                errors.append(f"{label}: {exc}")
        raise RuntimeError("no compatible Chromium browser could be launched: " + " | ".join(errors))

    @staticmethod
    def _activate_tab(page, label: str) -> None:
        locator = page.get_by_text(label, exact=True)
        count = locator.count()
        if count == 0:
            raise RuntimeError(f"ECHEMI tab not found: {label}")
        last_error: Exception | None = None
        for index in range(count):
            try:
                locator.nth(index).click(force=True, timeout=5000)
                page.wait_for_timeout(1200)
                return
            except Exception as exc:  # pragma: no cover - browser dependent
                last_error = exc
        raise RuntimeError(f"ECHEMI tab could not be activated: {label}: {last_error}")

    def _fetch_rendered_browser_bundle(self, item: SourceItem) -> bytes:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("playwright is required for ECHEMI browser-rendered acquisition") from exc

        with sync_playwright() as playwright:  # pragma: no cover - live browser integration
            browser = self._launch_system_browser(playwright)
            try:
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1100},
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page.goto(item.source_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                visible_text = page.locator("body").inner_text(timeout=10000)
                return visible_text.encode("utf-8")
            finally:
                browser.close()

    @classmethod
    def _has_full_rendered_coverage(cls, records: list[ExtractedRecord]) -> bool:
        sections = {str(record.data.get("source_section") or "") for record in records}
        return all(tab in sections for tab in cls._TAB_NAMES)

    _CURVE_CONTENT_MARKERS = (
        "Toluene diisocyanate More International Price",
        "Toluene diisocyanate Domestic Price",
    )

    @staticmethod
    def _wrap_rendered_text(text: str) -> bytes:
        # Stored as text/html so the raw evidence store and extract_html() are unchanged;
        # extract_html strips tags and unescapes, which yields the visible text again.
        return (
            '<html><body data-acquisition="edge-cdp"><pre>'
            + html_lib.escape(text)
            + "</pre></body></html>"
        ).encode("utf-8")

    def _fetch_rendered_edge(self, item: SourceItem) -> bytes:
        from app.sources.edge_cdp import fetch_rendered_text

        text = fetch_rendered_text(item.source_url, content_markers=self._CURVE_CONTENT_MARKERS)
        return self._wrap_rendered_text(text)

    @staticmethod
    def _is_challenge(payload: bytes) -> bool:
        from app.sources.edge_cdp import is_challenge

        return is_challenge(payload.decode("utf-8", "ignore"))

    def fetch_item(self, item: SourceItem) -> bytes:
        # Plain HTTP first, Edge only when that is refused.
        #
        # It was the other way round. On 8 September 2026 plain HTTP was challenged and a real
        # off-screen Edge was not, so Edge became the primary path. On 18 September the position had
        # reversed: a diagnostic on the production reader was served the verification page on all
        # five ECHEMI URLs tried, home page included, inside a second - and the same URL returned the
        # full price table to an ordinary HTTP request with no browser at all.
        #
        # So neither path is reliably the right one, and which is cannot be settled once and written
        # into the code. This tries the cheap one, checks what came back, and keeps the expensive one
        # for when the cheap one is refused. A challenge is recognised either way and reported as a
        # source failure; nothing here answers one.
        from app.sources.edge_cdp import EdgeUnavailable

        plain: bytes | None = None
        try:
            plain = self._fetch_http(item)
        except Exception:
            plain = None
        if plain is not None and not self._is_challenge(plain):
            return plain

        try:
            return self._fetch_rendered_edge(item)
        except EdgeUnavailable:
            if plain is not None:
                return plain
            raise

    def fetch(self) -> bytes:
        return self.fetch_item(self.discover()[0])

    @staticmethod
    def _date(text: str) -> datetime | None:
        try:
            return datetime.strptime(text.strip(), "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @classmethod
    def _first_date(cls, text: str) -> datetime | None:
        match = re.search(cls._DATE_RE, text)
        return cls._date(match.group(0)) if match else None

    @staticmethod
    def _clean_lines(text: str) -> list[str]:
        return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]

    @classmethod
    def _rendered_sections(cls, html: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        pattern = re.compile(
            r'<section\s+data-echemi-tab="(?P<tab>[^"]+)"[^>]*>.*?'
            r'<pre>(?P<text>.*?)</pre>.*?</section>',
            re.I | re.S,
        )
        for match in pattern.finditer(html):
            tab = html_lib.unescape(match.group("tab"))
            text = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group("text")))
            sections.append((tab, text))
        return sections

    @staticmethod
    def _record(
        *,
        src: str,
        section: str,
        publication_date: datetime | None,
        geography: str,
        basis: str,
        price: float,
        currency: str,
        unit: str = "t",
        grade: str | None = None,
        producer: str | None = None,
        source_label: str | None = None,
    ) -> ExtractedRecord:
        data = {
            "type": "price",
            "product": "TDI",
            "market_geography": geography,
            "commercial_basis": basis,
            "price": price,
            "currency": currency,
            "unit": unit,
            "observation_type": price_kinds.ASSESSED,
            "observation_date": publication_date,
            "source_section": section,
        }
        if grade:
            data["grade"] = grade
        if producer:
            data["producer"] = producer
            data["origin"] = producer
        if source_label:
            data["source_label"] = source_label
        return ExtractedRecord(
            kind="price",
            source_url=src,
            publication_date=publication_date,
            data=data,
        )

    def _extract_rendered_bundle(self, html: str, src: str) -> list[ExtractedRecord]:
        sections = self._rendered_sections(html)
        if not sections:
            return []

        out: list[ExtractedRecord] = []
        for section, text in sections:
            lines = self._clean_lines(text)
            if section == "China Domestic Price":
                joined = " ".join(lines)
                price_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*Yuan/mt", joined, re.I)
                dt = self._first_date(joined)
                if price_match:
                    out.append(
                        self._record(
                            src=src,
                            section=section,
                            publication_date=dt,
                            geography="China",
                            basis="EXW",
                            price=float(price_match.group(1)),
                            currency="CNY",
                            source_label="Toluene diisocyanate China Domestic Price",
                        )
                    )

            elif section == "International Price":
                for index, line in enumerate(lines):
                    marker = re.search(r"Toluene diisocyanate\s+Price adjustment\s+(.+)$", line, re.I)
                    if not marker:
                        continue
                    region = marker.group(1).strip()
                    window = " ".join(lines[index : index + 4])
                    dt = self._first_date(window)
                    price_match = re.search(r"\b(FOB|CFR|CIF)\s*([0-9]+(?:\.[0-9]+)?)\s*USD/ton\b", window, re.I)
                    if not price_match:
                        continue
                    out.append(
                        self._record(
                            src=src,
                            section=section,
                            publication_date=dt,
                            geography=region,
                            basis=price_match.group(1).upper(),
                            price=float(price_match.group(2)),
                            currency="USD",
                            source_label=line,
                        )
                    )

            elif section in {"China Regional Price", "Enterprise Price"}:
                # These ECHEMI categories do not expose an Incoterm in the visible source.
                # Keep them out of the governed price database until a real Incoterm is known.
                continue

        seen: set[tuple] = set()
        deduped: list[ExtractedRecord] = []
        for record in out:
            key = (
                record.data.get("source_section"),
                record.data.get("market_geography"),
                record.data.get("commercial_basis"),
                record.data.get("grade"),
                record.data.get("producer"),
                record.data.get("price"),
                record.publication_date,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped

    def _extract_curve_page(self, text: str, src: str) -> list[ExtractedRecord]:
        if src not in {self.china_fob_curve_url, self.europe_curve_url}:
            return []

        international_marker = "Toluene diisocyanate More International Price"
        domestic_marker = "Toluene diisocyanate Domestic Price"
        if international_marker not in text or domestic_marker not in text:
            return []

        international_text = text.split(international_marker, 1)[1].split(domestic_marker, 1)[0]
        domestic_text = text.split(domestic_marker, 1)[1]
        domestic_text = re.split(r"Related Products(?:'|’)\s*", domestic_text, maxsplit=1, flags=re.I)[0]

        regions = r"Europe|South Asia|South Korea|China|Southeast Asia|Middle East"
        row = re.compile(
            rf"Toluene diisocyanate\s+(?P<region>{regions})\s+Price adjustment\s+"
            rf"(?P<basis>FOB|CFR|CIF)\s+(?P<price>[0-9]+(?:\.[0-9]+)?)\s+"
            rf"(?:(?:[-+−—]?\s*[0-9]+(?:\.[0-9]+)?)\s+)?USD/ton\s+"
            rf"(?P<date>{self._DATE_RE})",
            re.I,
        )

        out: list[ExtractedRecord] = []
        for match in row.finditer(international_text):
            region = match.group("region")
            basis = match.group("basis").upper()
            if src == self.europe_curve_url and not (region == "China" and basis == "FOB"):
                continue
            if src == self.china_fob_curve_url and region == "China" and basis == "FOB":
                continue
            dt = self._date(match.group("date"))
            out.append(self._record(
                src=src,
                section="International Price",
                publication_date=dt,
                geography=region,
                basis=basis,
                price=float(match.group("price")),
                currency="USD",
                source_label=f"Toluene diisocyanate {region} Price adjustment",
            ))

        if src == self.china_fob_curve_url:
            domestic = re.search(
                rf"(?P<price>[0-9]+(?:\.[0-9]+)?)\s+.*?Yuan/mt\s+(?P<date>{self._DATE_RE})",
                domestic_text,
                re.I,
            )
            if domestic:
                dt = self._date(domestic.group("date"))
                out.append(self._record(
                    src=src,
                    section="China Domestic Price",
                    publication_date=dt,
                    geography="China",
                    basis="EXW",
                    price=float(domestic.group("price")),
                    currency="CNY",
                    source_label="Toluene diisocyanate China Domestic Price",
                ))
        return out

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()
        curve = self._extract_curve_page(text, src)
        if curve:
            return curve

        rendered = self._extract_rendered_bundle(html, src)
        if rendered:
            return rendered

        out: list[ExtractedRecord] = []

        domestic = re.search(
            rf"Toluene diisocyanate\s+China Domestic Price.*?({self._DATE_RE}).*?([0-9]+(?:\.[0-9]+)?)\s*Yuan/mt",
            text,
            re.I,
        )
        domestic_reverse = re.search(
            rf"Toluene diisocyanate\s+China Domestic Price.*?([0-9]+(?:\.[0-9]+)?)\s*Yuan/mt.*?({self._DATE_RE})",
            text,
            re.I,
        )
        match = domestic or domestic_reverse
        if match:
            if domestic:
                dt = self._date(match.group(1))
                price = float(match.group(2))
            else:
                price = float(match.group(1))
                dt = self._date(match.group(2))
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
                        "price": price,
                        "currency": "CNY",
                        "unit": "t",
                        "observation_type": price_kinds.ASSESSED,
                        "observation_date": dt,
                    },
                )
            )

        regions = r"Europe|South Asia|South Korea|China|Southeast Asia|Middle East"
        label = rf"Toluene diisocyanate\s+(?:(?P<region_before>{regions})\s+Price adjustment|Price adjustment\s+(?P<region_after>{regions}))"
        patterns = [
            re.compile(
                rf"{label}.*?(?P<date>{self._DATE_RE}).*?(?P<basis>FOB|CFR|CIF)\s*(?P<price>[0-9]+(?:\.[0-9]+)?)\s*USD/ton",
                re.I,
            ),
            re.compile(
                rf"{label}.*?(?P<basis>FOB|CFR|CIF)\s*(?P<price>[0-9]+(?:\.[0-9]+)?)\s*USD/ton.*?(?P<date>{self._DATE_RE})",
                re.I,
            ),
        ]
        seen: set[tuple[str, str, float, datetime | None]] = set()
        for pattern in patterns:
            for match in pattern.finditer(text):
                region = match.group("region_before") or match.group("region_after")
                dt = self._date(match.group("date"))
                key = (region, match.group("basis").upper(), float(match.group("price")), dt)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    ExtractedRecord(
                        kind="price",
                        source_url=src,
                        publication_date=dt,
                        data={
                            "type": "price",
                            "product": "TDI",
                            "market_geography": region,
                            "commercial_basis": match.group("basis").upper(),
                            "price": float(match.group("price")),
                            "currency": "USD",
                            "unit": "t",
                            "observation_type": price_kinds.ASSESSED,
                            "observation_date": dt,
                        },
                    )
                )
        # Some ECHEMI curve/related-price pages render geography before the product label.
        alt = re.compile(
            rf"(?P<region>{regions})\s+Toluene diisocyanate\s+Price adjustment\s+"
            rf"(?P<basis>FOB|CFR|CIF)\s+(?P<price>[0-9]+(?:\.[0-9]+)?)"
            rf"(?:\s+[-+0-9.]+)?\s+USD/(?:ton|mt)(?:\s+(?P<date>{self._DATE_RE}))?",
            re.I,
        )
        for match in alt.finditer(text):
            dt = self._date(match.group("date")) if match.group("date") else self._first_date(text[match.end():match.end()+80])
            key = (match.group("region"), match.group("basis").upper(), float(match.group("price")), dt)
            if key in seen:
                continue
            seen.add(key)
            out.append(self._record(
                src=src, section="International Price", publication_date=dt,
                geography=match.group("region"), basis=match.group("basis").upper(),
                price=float(match.group("price")), currency="USD",
                source_label="ECHEMI curve/related price",
            ))
        return out
