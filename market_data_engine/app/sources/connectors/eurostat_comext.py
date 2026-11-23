"""What Europe actually paid for a feedstock, from the customs record.

Stefan, 1 October 2026, having agreed we should collect European feedstock prices and been told the
two free publishers carry no history: "So lets make them all then, including the eurostat comext."

The publishers give one monthly figure and start from nothing, so a test on them is two years away.
Comext is the other half of the answer: EU trade in goods by eight-digit product code, monthly, back
to January 1988, free under Eurostat's own re-use policy. Value in euro divided by quantity gives an
implied euro per tonne with decades behind it.

WHAT THIS NUMBER IS. The customs value declared from the commercial invoices of every shipment under
the code, divided by its weight: contract and spot together, every grade under the code. Stefan,
13 October 2026: these are market prices. Since v0.5.10 this connector is no longer used for
collection; the trade layer (app/trade/eurostat.py) collects these codes and stores market prices.

The import flow is CIF at the EU border by the definition of the statistic, so the basis is CIF and
is not a guess.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds

# Comext lives on its own dissemination host; the ordinary Eurostat base answers ERR_NOT_FOUND_4 for
# a DS- dataset.
BASE_URL = "https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data"
DATASET = "DS-045409"            # EU trade since 1988 by CN8

VALUE = "VALUE_IN_EUROS"
QUANTITY = "QUANTITY_IN_100KG"

IMPORTS = "1"
EXPORTS = "2"

# How much has to move in a month before a Comext unit value is a market price.
#
# Value over quantity is exact and Eurostat's figures are honest; what the answer MEANS depends on
# how much moved. The EU's entire declared export of dimethyl terephthalate in August 2010 was
# 82 kilograms for EUR 18,934, which the arithmetic faithfully reports as 230,902 EUR/t. The gate
# here asked only for a positive quantity, and said so in as many words: "No minimum volume."
#
# Measured on 19 October 2026 over the 5,811 extra-EU months then held across all twelve codes,
# each month against its OWN opposite leg in the SAME month - the same chemical, the same market,
# the same month, so neither the level of a chemical nor the march of thirty years can flatter it,
# and only months whose opposite leg itself moved at least 1,000 t are used as a yardstick:
#
#     under 1 t        7.18x the other leg      73% of months above 3x
#     1 - 5 t          8.97x                    74%
#     5 - 10 t        16.46x                    91%
#     10 - 25 t        2.86x                    50%
#     25 - 50 t        1.48x                    14%
#     50 - 100 t       1.31x                     0%   (a quarter still above 1.5x)
#     100 - 200 t      1.11x                     0%   (none above 1.5x)
#     200 - 400 t      1.08x                     0%
#     400 - 700 t      1.03x                     1%
#     700 - 1,000 t    1.04x                     2%
#     1,000 - 2,000 t  1.04x                     0%
#     over 5,000 t     0.99x                     0%
#
# 100 tonnes a month is where the median lands on the other leg and the distortion stops. It is
# measured, not chosen, and it is not UN Comtrade's 1,000 t because this is not Comtrade's
# population: a Comtrade row is one country's annual or monthly declaration under an HS6 code,
# this is the whole EU27's month under one CN8 code, and each was measured on its own data.
#
# A relative rule was measured beside it and rejected. Of the months at or above 100 t that are
# under half the median tonnage of the twelve months before them - the test this file already
# applies to the polyol partner series - only 1% are off their own neighbours by more than 2x,
# against 0% for the rest. It would have cost 395 months and bought nothing.
#
# Below the floor the TONNAGE is still a fact and is still stored. Only the claim that the unit
# value is a market price is withdrawn.
MARKET_QUANTITY_TONNES = 100.0


class EurostatComextConnector:
    """One CN8 code, one flow, monthly, as an implied euro per tonne."""

    allow_empty_parse = False

    def __init__(self, source_id: str, *, product: str, cn_code: str,
                 geography: str = "Europe", reporter: str = "EU27_2020",
                 partner: str = "EXT_EU27_2020", flow: str = IMPORTS):
        self.source_id = source_id
        self.product = product
        self.cn_code = cn_code
        self.geography = geography
        self.reporter = reporter
        self.partner = partner
        self.flow = flow
        self.source_url = self._url(VALUE)

    def _url(self, indicator: str) -> str:
        """Every month the dataset holds for this one series.

        No time filter. The first attempt carried `sinceTimePeriod`, which the Comext host answers
        with a 400 - the parameter it accepts is `TIME_PERIOD`, and it takes a list of exact periods
        rather than a starting point. Asking for no period at all returns the whole series, which is
        what we are here for: the publishers carry three months of history and this is the source
        that carries thirty years. With the reporter, partner, product, flow and one indicator all
        pinned, that is one number a month since 1988 and nothing like a large response.
        """
        return (f"{BASE_URL}/{DATASET}?format=JSON&lang=EN&freq=M"
                f"&reporter={self.reporter}&partner={self.partner}"
                f"&product={self.cn_code}&flow={self.flow}"
                f"&indicators={indicator}")

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        """Both indicators, asked for one at a time and paired here.

        One indicator per request keeps every dimension but time at size one, so a period's value is
        found by its own index rather than by arithmetic over six dimensions. The arithmetic would be
        right until the day Eurostat reorders a dimension, and then quietly wrong.
        """
        out = {}
        for name, indicator in (("value", VALUE), ("quantity", QUANTITY)):
            response = httpx.get(self._url(indicator), timeout=60.0,
                                 headers={"Accept": "application/json"})
            response.raise_for_status()
            out[name] = response.json()
        return json.dumps(out).encode("utf-8")

    @staticmethod
    def _series(cube: dict) -> dict[str, float]:
        """Period -> number, from a JSON-stat cube with one cell per period."""
        sizes = dict(zip(cube.get("id") or [], cube.get("size") or []))
        wide = {name: size for name, size in sizes.items() if name != "time" and size > 1}
        if wide:
            raise ValueError(f"Comext answered more than one series at once: {wide}")
        index = (((cube.get("dimension") or {}).get("time") or {}).get("category") or {}).get("index") or {}
        values = cube.get("value")
        if isinstance(values, list):
            values = {str(i): v for i, v in enumerate(values)}
        out: dict[str, float] = {}
        for period, position in index.items():
            cell = (values or {}).get(str(position))
            if cell is not None:
                out[str(period)] = float(cell)
        return out

    @staticmethod
    def _month(period: str) -> datetime | None:
        """A month label from Comext, in either spelling, to the first of that month.

        Comext writes its monthly periods as "2026-09". This read only the "2026M09" spelling that
        other Eurostat endpoints use, so on 2 October 2026 all five Comext sources fetched a valid
        468-period cube, found no period it recognised, skipped every record and reported the source
        as changed. The response was right from the first run; this line was wrong.

        A quarter ("2026-Q3") and a year ("2026") are still left alone: a unit value averaged over a
        quarter is a different number and does not belong in a monthly series.
        """
        text = str(period).strip()
        if "M" in text:
            year, _, month = text.partition("M")
        elif "-" in text:
            year, _, month = text.partition("-")
        else:
            return None
        if not (year.isdigit() and month.isdigit() and len(year) == 4):
            return None
        if not 1 <= int(month) <= 12:
            return None
        return datetime(int(year), int(month), 1, tzinfo=timezone.utc)

    def extract_html(self, payload: str, source_url: str | None = None) -> list[ExtractedRecord]:
        document = json.loads(payload)
        values = self._series(document["value"])
        quantities = self._series(document["quantity"])
        out: list[ExtractedRecord] = []
        for period, euros in sorted(values.items()):
            hundred_kg = quantities.get(period)
            observed = self._month(period)
            # A month with no quantity cannot give a unit value, and a month under
            # MARKET_QUANTITY_TONNES has one that is not a market price - see the measurement at
            # the top of this file. This connector is no longer used for collection; the trade
            # layer is, and it keeps the small month as a feature record. Here there is nowhere to
            # keep it, so the month simply produces no price.
            if observed is None or not hundred_kg or hundred_kg <= 0:
                continue
            tonnes = hundred_kg / 10.0
            if tonnes < MARKET_QUANTITY_TONNES:
                continue
            out.append(ExtractedRecord(
                kind="price",
                source_url=source_url or self.source_url,
                publication_date=observed,
                data={
                    "type": "price",
                    "product": self.product,
                    "market_geography": self.geography,
                    "commercial_basis": "CIF",
                    "price": round(euros / tonnes, 2),
                    "currency": "EUR",
                    "unit": "t",
                    # A declared import, so a deal rather than an assessment - see `price_kinds`.
                    "observation_type": price_kinds.DEAL,
                    "observation_date": observed,
                    "source_label": (f"EU imports of {self.product} (CN {self.cn_code}), "
                                     f"{observed:%B %Y}: EUR {euros:,.0f} for {tonnes:,.0f} t"),
                },
            ))
        return out


# --- European polyether polyol: what China sold into the EU, and what everyone else did ----------
#
# Stefan, 13 October 2026: "the idea is that you implement this new source in our application, plus
# retrieve those 55 months of history too." His instruction set the terms: Eurostat Comext is the
# primary source, PetroApex only the cross-check, and the result is called a Customs Import Unit
# Value - never a European polyol price, because CN 39072920 holds every polyether alcohol in primary
# form, not one commercial grade.
#
# The code exists from January 2022 only. The 2022 Harmonised System split the old polyether heading,
# and the months before that sit under a predecessor code that must not be chained on without a
# documented correspondence (Stefan's instruction, section 5). This connector reads 39072920 alone.
#
# Three series come out of four requests, and they stay three:
#   China       - EU27 imports from China. A published Eurostat figure.
#   Extra-EU    - EU27 imports from every non-EU partner together. A published Eurostat figure.
#   Other       - Extra-EU minus China, value and quantity both. NOT published by Eurostat: a residual
#                 HTC derives, so it carries its own observation type and says so.
# Each has its own origin, so each is its own market identity and none is ever averaged with another.
#
# Validation, 13 October 2026, against Eurostat's own numbers fetched that morning: July 2026 gives
# China EUR 1,485.95/t, China 62.0% of extra-EU tonnes, other origins EUR 2,394.38/t. PetroApex
# printed 1,486, 62.0% and 2,394. The test file pins these to the real figures.

CHINA = "CN"
EXTRA_EU = "EXT_EU27_2020"

ORIGIN_CHINA = "China"
ORIGIN_EXTRA_EU = "Extra-EU27"
ORIGIN_OTHER = "Extra-EU27 excluding China"

PUBLISHED_TYPE = "trade_unit_value"
DERIVED_TYPE = "derived_trade_unit_value"

BASIS_LABEL = "Customs Import Unit Value"
PRIMARY_SOURCE = "Eurostat Comext"

# A month whose import tonnage is under half the median of the twelve before it is flagged. Measured
# on the real series on 13 October 2026, that marks China in November and December 2024 (3,916 t and
# 4,410 t against a 9,107 t median) and nothing else. In a month that thin the unit value can move on
# product mix alone, so the reading is kept and marked rather than dropped or trusted.
LOW_VOLUME_SHARE_OF_MEDIAN = 0.5
LOW_VOLUME_MIN_HISTORY = 6

# Comext publishes month M around the middle of month M+2: July 2026 appeared with the dataset update
# of 15 September 2026. For the newest month the dataset's own update stamp is the publication date.
# For older months fetched today the real first-publication date is not in the response, so it is
# set by that measured rule and marked "estimated". Setting it to the observation month itself - which
# the single-series connector above does - would let a backtest use July's customs figure on 1 July.
PUBLICATION_LAG_MONTHS = 2
PUBLICATION_DAY = 15


def _add_months(moment: datetime, months: int) -> datetime:
    index = moment.year * 12 + (moment.month - 1) + months
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=timezone.utc)


def _updated_stamp(cube: dict) -> datetime | None:
    text = str(cube.get("updated") or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def low_volume_months(tonnes_by_month: dict[str, float]) -> set[str]:
    """Months whose tonnage is under half the median of the twelve months before them."""
    months = sorted(tonnes_by_month)
    flagged: set[str] = set()
    for position, month in enumerate(months):
        before = [tonnes_by_month[m] for m in months[max(0, position - 12):position]]
        if len(before) < LOW_VOLUME_MIN_HISTORY:
            continue
        ordered = sorted(before)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        if tonnes_by_month[month] < LOW_VOLUME_SHARE_OF_MEDIAN * median:
            flagged.add(month)
    return flagged
