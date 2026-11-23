"""Global trade layer: UN Comtrade, HS6, reporter countries, monthly and annual kept apart.

Read through Comtrade's public API, which needs no key: one period per request, at most 500 rows,
and a rate limit that answers 429 when asked too quickly (measured 13 October 2026). So each request
names every reporter and every HS6 code at once - 7 reporters x 13 codes x 2 flows is under 200 rows
- and the connector waits between periods. A refusal states `Retry-After` in seconds and that is
what is waited: measured 19 November 2026 it asks for 1, and this used to sleep 8 and then more.

A unit value is USD / net-weight tonnes and nothing else. Where the net weight is missing or zero
the reading is kept with no unit value, marked "n/r", never estimated from another field. Where
Comtrade itself estimated the weight (`isNetWgtEstimated`), the unit value is calculated and flagged,
because that is how PetroApex's China figures reproduce: 815,752 t at USD 1,781/t in 2020 (HS 390720,
estimated weight) and 2,848,002 t at USD 1,275/t in 2025 (HS 390729, reported weight).

Prices are stored as market prices: the reporter against the World, imports CIF and exports FOB, in
USD per tonne. Monthly figures are the line; an annual figure is kept only for a year the monthly
series of that market does not cover, so no market holds two datapoints for one period. Bilateral
flows are kept as variables. Comtrade's EU figures are not used: the EU side belongs to Eurostat.
HS 390720 (to 2021) continues the 390729 line with its mapping recorded on every record.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds
from app.sources.connectors.eurostat_comext import _add_months
from app.trade import materials as m

BASE = "https://comtradeapi.un.org/public/v1/preview/C"
PRIMARY_SOURCE = "UN Comtrade"
SOURCE_DATASET = "UN Comtrade public API, commodities (C), HS"
SOURCE_TYPE = "Official trade statistics"

# How much has to move before a customs line is a market price.
#
# The division is exact and the inputs are honest; what they MEAN depends on the size of the
# shipment. Türkiye's entire declared propylene oxide import for 2004 was 21 kg for USD 754, which
# value-over-weight faithfully reports as 35,905 USD/t. The old gate asked only for a positive
# weight, and said so in as many words - "No minimum volume applies."
#
# Measured over the 19,151 Comtrade rows held on 17 October 2026, each against its own material's
# level (the median unit value of shipments above 1,000 t, which recovers benzene at 766, toluene
# 770, ethylene oxide 1,999, propylene oxide 1,325 and polyether polyol 2,231 - all correct):
#
#     under 100 kg        34.58x the level      90% of rows above 3x
#     0.1 - 1 t           12.50x                80%
#     1 - 10 t             3.16x                51%
#     10 - 100 t           1.45x                15%
#     100 - 1,000 t        1.25x                 7%
#     1,000 - 10,000 t     1.02x                 1%
#     over 10,000 t        0.98x                 0%
#
# 1,000 t is where the median lands on the level and the bad rows all but disappear. It keeps
# 12,527 of the 19,151 rows.
#
# Below the floor the TONNAGE is still a fact and is still stored, because a 21 kg import did
# happen. Only the unit value is withheld from the price store; it stays on the feature record as
# a variable, so nothing is destroyed and a better rule can be applied to it later.
MARKET_QUANTITY_TONNES = 1000.0


FLOW_WORD = {"X": "Export", "M": "Import"}
WORLD = "0"

# HS6 code -> (material, predecessor spec or None). A predecessor code is asked for only in periods
# up to the last one it was in force.
def _codes() -> dict[str, tuple[m.Material, m.Predecessor | None]]:
    out: dict[str, tuple[m.Material, m.Predecessor | None]] = {}
    for material in m.MATERIALS:
        out[material.hs] = (material, None)
        if material.hs_predecessor and material.hs_predecessor.hs:
            out[material.hs_predecessor.hs] = (material, material.hs_predecessor)
    return out


CODES = _codes()


def _key(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


# Waiting out a refusal. The API answers 429 with `Retry-After` in whole seconds, so that is what
# is waited; `RATE_LIMIT_MARGIN_SECONDS` on top because a wait of exactly the stated second lands on
# the boundary the server is measuring. The fallback is only for a refusal that says nothing, and it
# still escalates, so a server that is genuinely angry is not hammered.
RATE_LIMIT_ATTEMPTS = 6
RATE_LIMIT_FALLBACK_SECONDS = 8.0
RATE_LIMIT_CEILING_SECONDS = 30.0
RATE_LIMIT_MARGIN_SECONDS = 1.0


def _wait_asked_for(response, fallback: float) -> float:
    """How long the refusal asks us to wait, or `fallback` where it does not say.

    `Retry-After` is allowed to be an HTTP date as well as a number of seconds (RFC 9110). This API
    sends a number; a date is not parsed here, it falls back, because guessing a clock skew is worse
    than waiting the fallback. Capped at the ceiling so a header of 3600 cannot park a whole window.
    """
    told = str(response.headers.get("retry-after") or "").strip()
    try:
        asked = float(told)
    except ValueError:
        return fallback
    if asked < 0:
        return fallback
    return min(asked + RATE_LIMIT_MARGIN_SECONDS, RATE_LIMIT_CEILING_SECONDS)


class ComtradeConnector:
    """One frequency, one window of periods, every configured reporter and material."""

    allow_empty_parse = True

    # The background collector gives a trade source 300 seconds and kills it after that, losing what
    # it had fetched. So a source stops asking at 240 seconds and stores what it has, leaving time to
    # start up and to save. It was 75 inside a 90-second limit, and with start-up and saving on top
    # every Comtrade source ran out of time on 13 October 2026.
    BUDGET_SECONDS = 240.0

    def __init__(self, source_id: str, *, frequency: str, first: str | None, last: str | None = None,
                 trailing_months: int | None = None, bilateral: bool = False, bilateral_only: bool = False,
                 pause: float = 3.0, now: datetime | None = None, budget_seconds: float | None = None):
        if frequency not in ("A", "M"):
            raise ValueError("frequency is A or M")
        self.source_id = source_id
        self.frequency = frequency
        self.first, self.last = first, last
        self.trailing_months = trailing_months
        self.bilateral = bilateral or bilateral_only
        self.bilateral_only = bilateral_only
        self.pause = pause
        self._now = now
        self.budget_seconds = self.BUDGET_SECONDS if budget_seconds is None else budget_seconds
        self.product = "Trade layer"
        self.source_url = f"{BASE}/{frequency}/HS"

    # -- periods ---------------------------------------------------------------------------------

    def periods(self) -> list[str]:
        now = self._now or datetime.now(timezone.utc)
        if self.frequency == "A":
            last = int(self.last) if self.last else now.year - 1
            return [str(y) for y in range(int(self.first), last + 1)]
        latest = _add_months(datetime(now.year, now.month, 1, tzinfo=timezone.utc), -1)
        if self.trailing_months:
            end = latest
            start = _add_months(end, -(self.trailing_months - 1))
        else:
            start = datetime(int(self.first[:4]), int(self.first[4:]), 1, tzinfo=timezone.utc)
            end = datetime(int(self.last[:4]), int(self.last[4:]), 1, tzinfo=timezone.utc)
            end = min(end, latest)
        out, cursor = [], start
        while cursor <= end:
            out.append(f"{cursor:%Y%m}")
            cursor = _add_months(cursor, 1)
        return out

    def codes_for(self, period: str) -> list[str]:
        year = int(period[:4])
        out = []
        for code, (_, predecessor) in CODES.items():
            if predecessor is not None:
                if year > int(predecessor.valid_to[:4]):
                    continue
            else:
                material = CODES[code][0]
                if material.hs_predecessor and material.hs_predecessor.hs \
                        and year <= int(material.hs_predecessor.valid_to[:4]):
                    continue
            out.append(code)
        return sorted(out)

    def _url(self, period: str, *, bilateral: bool) -> str:
        if bilateral:
            reporters = ",".join(m.BILATERAL_REPORTERS)
            partners = ",".join(m.EU_PARTNERS)
            codes = ",".join(c for c in self.codes_for(period) if CODES[c][0].key in m.BILATERAL_MATERIALS)
            flows = "X"
        else:
            reporters = ",".join(m.REPORTERS)
            partners = WORLD
            codes = ",".join(self.codes_for(period))
            flows = "X,M"
        return (f"{BASE}/{self.frequency}/HS?reporterCode={reporters}&period={period}"
                f"&partnerCode={partners}&cmdCode={codes}&flowCode={flows}")

    # -- fetching --------------------------------------------------------------------------------

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def _get(self, url: str) -> dict:
        """One period, with a refusal waited out for as long as the refusal asks.

        Measured 19 November 2026 against the live API: a 429 carries `Retry-After: 1` and a body
        reading "Rate limit is exceeded. Try again in 1 seconds." This slept 8 seconds, then 12,
        18, 27, 30 and 30 - up to 125 seconds of waiting for a limit that wanted one, inside a
        240-second budget for the whole window. The server knows how long it wants; the escalating
        guess is kept only for a refusal that does not say.
        """
        fallback = RATE_LIMIT_FALLBACK_SECONDS
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            response = httpx.get(url, timeout=60.0, headers={"Accept": "application/json"})
            if response.status_code == 429:
                time.sleep(_wait_asked_for(response, fallback))
                fallback = min(fallback * 1.5, RATE_LIMIT_CEILING_SECONDS)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"{m.SOURCE_UNAVAILABLE}: rate limit not lifted after "
                           f"{RATE_LIMIT_ATTEMPTS} attempts")

    def fetch_item(self, item: SourceItem) -> bytes:
        responses: dict[str, dict] = {}
        errors: list[str] = []
        started = time.monotonic()
        if self.bilateral_only:
            kinds = ("bilateral",)
        elif self.bilateral and self.frequency == "A":
            kinds = ("world", "bilateral")
        else:
            kinds = ("world",)
        for period in self.periods():
            for kind in kinds:
                if time.monotonic() - started > self.budget_seconds:
                    errors.append(f"{period} {kind}: not reached within {self.budget_seconds:.0f} s")
                    continue
                try:
                    body = self._get(self._url(period, bilateral=kind == "bilateral"))
                    if body.get("error"):
                        errors.append(f"{period} {kind}: {m.API_ERROR}: {body['error']}")
                    responses.setdefault(period, {})[kind] = body
                except Exception as exc:  # one period failing does not lose the others
                    errors.append(f"{period} {kind}: {exc}")
                if self.pause:
                    time.sleep(self.pause)
        if not responses:
            raise RuntimeError(f"{m.SOURCE_UNAVAILABLE}: " + "; ".join(errors[:5]))
        return json.dumps({"frequency": self.frequency, "responses": responses, "errors": errors,
                           "retrieved_at": datetime.now(timezone.utc).isoformat()}).encode("utf-8")

    # -- reading ---------------------------------------------------------------------------------

    def _observed(self, period: str) -> datetime:
        if self.frequency == "A":
            return datetime(int(period[:4]), 1, 1, tzinfo=timezone.utc)
        return datetime(int(period[:4]), int(period[4:6]), 1, tzinfo=timezone.utc)

    def _published(self, period: str, retrieved: datetime) -> datetime:
        """Comtrade's response carries no release date per figure, so availability is estimated:
        an annual figure from 1 July of the following year, a monthly one three months after its
        month. Never later than the moment it was actually retrieved."""
        if self.frequency == "A":
            estimate = datetime(int(period[:4]) + 1, 7, 1, tzinfo=timezone.utc)
        else:
            estimate = _add_months(self._observed(period), 3)
        return min(estimate, retrieved)

    def rows(self, payload: str) -> list[dict]:
        document = json.loads(payload)
        out: list[dict] = []
        for period, kinds in sorted(document.get("responses", {}).items()):
            for kind, body in kinds.items():
                for row in body.get("data") or []:
                    code = str(row.get("cmdCode") or "")
                    if code not in CODES:
                        continue
                    if str(row.get("partner2Code", 0)) not in ("0", "None") or str(row.get("motCode", 0)) != "0" \
                            or str(row.get("customsCode", "C00")) != "C00":
                        continue
                    out.append({**row, "_kind": kind, "_period": str(row.get("period") or period)})
        return out

    def extract_html(self, payload: str, source_url: str | None = None) -> list[ExtractedRecord]:
        document = json.loads(payload)
        retrieved = datetime.fromisoformat(document.get("retrieved_at")) if document.get("retrieved_at") \
            else datetime.now(timezone.utc)
        url = source_url or self.source_url
        annual = self.frequency == "A"
        freq_word = "Annual" if annual else "Monthly"
        series: dict[tuple, dict[str, dict]] = {}
        out: list[ExtractedRecord] = []
        for row in self.rows(payload):
            code = str(row["cmdCode"])
            material, predecessor = CODES[code]
            reporter = m.REPORTERS.get(str(row.get("reporterCode")))
            if reporter is None:
                continue
            flow = FLOW_WORD.get(str(row.get("flowCode")))
            if flow is None:
                continue
            partner_code = str(row.get("partnerCode"))
            partner = "World" if partner_code == WORLD else m.EU_PARTNERS.get(partner_code)
            if partner is None:
                continue
            period = row["_period"]
            value = row.get("primaryValue")
            weight_kg = row.get("netWgt")
            tonnes = (float(weight_kg) / 1000.0) if weight_kg not in (None, "") else None
            # Two different questions. Is there a quantity at all - which decides whether the
            # tonnage is a fact and whether a unit value can be computed. And is it a market
            # quantity - which decides whether that unit value is a PRICE.
            has_quantity = value not in (None, 0) and tonnes is not None and tonnes > 0
            priceable = has_quantity and tonnes >= MARKET_QUANTITY_TONNES
            estimated_weight = bool(row.get("isNetWgtEstimated"))
            unit_value = float(value) / tonnes if has_quantity else None
            series_key = (material.key, code, reporter, flow, partner)
            # Tonnage changes are computed at any size: a small flow is still a real flow.
            series.setdefault(series_key, {})[period] = {"tonnes": tonnes if has_quantity else None,
                                                         "unit_value": unit_value}
            observed = self._observed(period)
            published = self._published(period, retrieved)
            mapping_status = predecessor.mapping_status if predecessor else "current code"
            statuses = [m.HISTORICAL_MAPPING_REQUIRED] if predecessor else []
            if document.get("errors"):
                statuses.append(m.API_ERROR)
            if not has_quantity:
                statuses.append(m.MISSING_QUANTITY)
            elif not priceable:
                statuses.append(m.BELOW_MARKET_QUANTITY)
            limitation = [material.limitation] if material.limitation else []
            if estimated_weight and has_quantity:
                limitation.append("Net weight estimated by UN Comtrade, not reported.")
            if has_quantity and not priceable:
                limitation.append(
                    f"{tonnes:,.3f} t declared, under the {MARKET_QUANTITY_TONNES:,.0f} t a unit "
                    "value needs to be a market price. The tonnage is kept; the unit value is not "
                    "stored as a price.")
            feature_base = f"ct_{material.key}_hs{code}_{_key(reporter)}_{flow.lower()}_{_key(partner)}_{freq_word.lower()}"
            record = {
                "material": material.key,
                "hs_code": code,
                "cn_code": None,
                "nomenclature": f"HS ({row.get('classificationCode') or 'unknown'})",
                "nomenclature_version": row.get("classificationCode"),
                "product_description": row.get("cmdDesc") or (predecessor.label if predecessor else None),
                "predecessor_mapping_status": mapping_status,
                "mapping_note": predecessor.note if predecessor else None,
                "reporter": reporter,
                "reporter_code": str(row.get("reporterCode")),
                "partner": partner,
                "partner_code": partner_code,
                "flow": flow,
                "frequency": freq_word,
                "observation_period": period,
                "observation_month": period,
                "observation_date": observed,
                "primary_source": PRIMARY_SOURCE,
                "source_dataset": SOURCE_DATASET,
                "source_type": SOURCE_TYPE,
                "trade_value": value,
                "customs_value_basis": "FOB" if flow == "Export" else "CIF",
                "currency": "USD",
                "net_weight": weight_kg,
                "net_weight_unit": "kg",
                "weight_estimated_by_un_comtrade": estimated_weight,
                "quantity": row.get("qty"),
                "quantity_unit_code": row.get("qtyUnitCode"),
                "quantity_unit": row.get("qtyUnitAbbr"),
                "quantity_estimated": bool(row.get("isQtyEstimated")),
                "alt_quantity": row.get("altQty"),
                "alt_quantity_unit_code": row.get("altQtyUnitCode"),
                "is_reported": row.get("isReported"),
                "tonnes": round(tonnes, 3) if tonnes is not None else None,
                "unit_value": round(unit_value, 2) if unit_value is not None else m.NO_RELIABLE_UNIT_VALUE,
                "unit_value_currency": "USD",
                "unit_value_basis": "per tonne of net weight",
                "data_basis": m.CUSTOMS_EXPORT_UNIT_VALUE if flow == "Export" else m.CUSTOMS_IMPORT_UNIT_VALUE,
                "calculation_method": "primaryValue (USD) / (netWgt kg / 1000)",
                "observation_status": (None if priceable else
                                       m.MISSING_QUANTITY if not has_quantity
                                       else m.BELOW_MARKET_QUANTITY),
                "refresh_statuses": statuses,
                "limitation_note": " ".join(limitation) or None,
                "retrieved_at": retrieved.isoformat(),
                "source_updated": None,
                "fetch_errors": document.get("errors") or [],
                "publication_date_basis": ("estimated: 1 July of the following year" if annual
                                           else "estimated: observation month + 3 months"),
                "record_revisions": True,
                "raw_signature": f"{value}|{weight_kg}",
            }
            # The market price is the reporter against the World. A bilateral flow is the same
            # reporter's trade split by destination; drawn beside it, it would be a second datapoint
            # for one market and period, so it is kept as a variable.
            #
            # And the flow has to be a market quantity. This line used to read "No minimum volume
            # applies", which is how a 21 kg bottle became a benzene price.
            if priceable and partner == "World":
                export = flow == "Export"
                # A customs declaration is a trade that happened, so its unit value is a DEAL, not
                # a publisher's assessment of the market. Filed as `market_price` it put 18,234
                # declared trades onto the assessed line (masterplan A, item 6); migration 0058
                # moved the stored rows and this stops them coming back.
                observation_type = price_kinds.DEAL
                out.append(ExtractedRecord(
                    kind="price", source_url=url, publication_date=published,
                    data={**record, "type": "price", "product": material.product,
                          "grade": None,
                          # An annual figure is a fallback: the store keeps it only for a year the
                          # monthly series of the same market does not cover, so a market never
                          # holds a year and its January as two datapoints for one date.
                          "annual_fallback": annual,
                          "supersedes_annual": not annual,
                          "market_geography": reporter,
                          "origin": reporter if export else None,
                          "destination": (partner if partner != "World" else None) if export else reporter,
                          "commercial_basis": "FOB" if export else "CIF",
                          "price": round(unit_value, 2), "unit": "t",
                          "observation_type": observation_type,
                          "source_label": (f"{reporter} {material.product} {flow} Unit Value - {partner} "
                                           f"(HS {code}), {period}: USD {float(value):,.0f} for {tonnes:,.1f} t")}))
            out.append(ExtractedRecord(
                kind="feature", source_url=url, publication_date=published,
                data={**record, "type": "feature", "product": material.product,
                      "feature_type": f"{feature_base}_record",
                      # The unit value stays on the feature record whatever the size, as a
                      # variable rather than a price, so a small flow is recorded and not lost.
                      "value": round(unit_value, 2) if has_quantity else None}))
            if tonnes is not None and tonnes > 0:
                out.append(ExtractedRecord(
                    kind="feature", source_url=url, publication_date=published,
                    data={**record, "type": "feature", "product": material.product,
                          "feature_type": f"{feature_base}_tonnes", "value": round(tonnes, 3),
                          "data_basis": m.CUSTOMS_TRADE_VOLUME, "calculation_method": "netWgt kg / 1000"}))
        out.extend(self._changes(series, retrieved, url))
        return out

    def _changes(self, series, retrieved, url) -> list[ExtractedRecord]:
        annual = self.frequency == "A"
        freq_word = "annual" if annual else "monthly"
        lags = (("yoy", 1),) if annual else (("mom", 1), ("yoy", 12))
        out: list[ExtractedRecord] = []
        for (material_key, code, reporter, flow, partner), periods in series.items():
            base = f"ct_{material_key}_hs{code}_{_key(reporter)}_{flow.lower()}_{_key(partner)}_{freq_word}"
            for label, lag in lags:
                for field, name in (("tonnes", "tonnes"), ("unit_value", "unit_value")):
                    for period, row in periods.items():
                        prior = self._prior(period, lag)
                        now_value, before = row[field], (periods.get(prior) or {}).get(field)
                        if not now_value or not before:
                            continue
                        out.append(ExtractedRecord(
                            kind="feature", source_url=url, publication_date=self._published(period, retrieved),
                            data={"type": "feature", "feature_type": f"{base}_{name}_{label}",
                                  "material": material_key, "hs_code": code, "reporter": reporter,
                                  "flow": flow, "partner": partner, "frequency": freq_word.capitalize(),
                                  "observation_period": period, "observation_month": period,
                                  "value": round(now_value / before - 1.0, 6),
                                  "data_basis": m.DERIVED_TRADE_INDICATOR, "primary_source": PRIMARY_SOURCE,
                                  "calculation_method": f"value / value {lag} period(s) earlier - 1",
                                  "record_revisions": True, "raw_signature": f"{now_value}|{before}"}))
        return out

    def _prior(self, period: str, lag: int) -> str:
        if self.frequency == "A":
            return str(int(period) - lag)
        moment = _add_months(self._observed(period), -lag)
        return f"{moment:%Y%m}"
