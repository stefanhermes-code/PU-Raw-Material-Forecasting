from __future__ import annotations

import re
from datetime import datetime, timezone

from app.sources.connectors.public_tdi import PublicTDIExcerptConnector
from raw_material_forecasting import price_kinds


class ChemAnalystTDIConnector(PublicTDIExcerptConnector):
    """ChemAnalyst public TDI page: a quarterly narrative per region.

    Each "For the Quarter Ending <Month YYYY>" block names one country per region
    (e.g. USA, Japan, Germany) and gives that country's quarterly average in USD/MT;
    other countries (China, South Korea, ...) only get a percentage change. The
    previous parser took the first USD figure on the page and labelled it China,
    which put a Japan/USA number into the China identity. Now every record carries
    the country the page actually names, and is typed as a quarterly average so it
    never mixes with daily market prices. Verified against the live page 2026-09-08.
    """

    # Germany stays Germany. Stefan, 2 October 2026: asked whether this page's German quarterly
    # average should be filed as Europe so a reader opening TDI Europe would see it, he ruled to
    # leave it as TDI Germany. It would have appeared there as its own line rather than joining the
    # monthly European lines, and a quarterly average beside monthly prices reads as one series.
    source_id = "chemanalyst_tdi"
    source_url = "https://www.chemanalyst.com/Pricing-data/toluene-diisocyanate-35"
    _MONTHS = {name: i for i, name in enumerate(
        "January February March April May June July August September October November December".split(), 1)}
    _QUARTER_RE = re.compile(r"For the Quarter Ending\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})", re.I)
    _COUNTRY_RE = re.compile(r"\bIn (?:the )?([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)(?: \([^)]*\))?, (?:the )?Toluene Diisocyanate", re.I)
    _AVG_RE = re.compile(r"average Toluene Diisocyanate price for the quarter was approximately USD\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*/\s*MT", re.I)
    _COUNTRY_ALIASES = {"USA": "United States", "US": "United States", "UK": "United Kingdom"}

    def extract_html(self, html: str, source_url: str):
        text = self.visible_text(html)
        quarters = list(self._QUARTER_RE.finditer(text))
        out = []
        seen = set()
        for index, q in enumerate(quarters):
            end = quarters[index + 1].start() if index + 1 < len(quarters) else len(text)
            block = text[q.end():end]
            dt = datetime(int(q.group(2)), self._MONTHS[q.group(1).capitalize()], 1, tzinfo=timezone.utc)
            # Walk the block: remember the last named country, attribute the next quarterly average to it.
            events = sorted(
                [(m.start(), "country", m.group(1)) for m in self._COUNTRY_RE.finditer(block)]
                + [(m.start(), "avg", m.group(1)) for m in self._AVG_RE.finditer(block)]
            )
            country = None
            for _, kind, value in events:
                if kind == "country":
                    country = self._COUNTRY_ALIASES.get(value, value)
                    continue
                if country is None:
                    continue
                price = float(value.replace(",", ""))
                key = (country, dt)
                if key in seen:
                    continue
                seen.add(key)
                record = self.record(source_url, price=price, dt=dt, geography=country, currency="USD", basis="UNSPECIFIED")
                record.data["product"] = "TDI"
                record.data["grade"] = None
                # The quarter is the PERIOD the reading covers, now a property of the reading
                # (`price_kinds.period_of`), so the kind is what it always was: an
                # assessment. "quarterly_average" answered two questions at once.
                record.data["observation_type"] = price_kinds.ASSESSED
                record.data["source_label"] = f"ChemAnalyst quarterly average, {country}, quarter ending {q.group(1)} {q.group(2)}"
                out.append(record)
        return out
