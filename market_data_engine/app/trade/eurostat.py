"""EU trade layer: Eurostat Comext DS-045409, one material per source, imports and exports.

For every material the connector reads EU27 trade with all extra-EU partners together, both flows,
value and quantity, every month Eurostat holds. Configured partners (China, for polyether polyol)
add a partner series and the derived residual. A configured supplier breakdown reads every partner
once, for the top-three supplier share.

What comes out:

  price observations   the extra-EU import unit value (Europe, CIF, EUR/t) and export unit value
                       (Europe, FOB, EUR/t), stored as market prices (Stefan, 13 October 2026: these
                       are declared invoice values and are treated as market prices, no new type).
  feature observations one full record per series and month - raw value and quantity, method, flags -
                       for the extra-EU total, exports, each partner (China for polyol) and the derived
                       other-origin residual; volumes, partner share, top-three share, import/export
                       balance, and month-on-month and year-on-year changes, each its own variable.

Only the extra-EU total is drawn as the price. China and the residual are the same month of the same
market split by origin, and drawing them beside it would make three datapoints of one.

Every month is re-read on every run, so new months arrive and revisions are seen; the store records a
changed month as a revision instead of overwriting it silently. Checked weekly; the data are monthly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem
from raw_material_forecasting import price_kinds
from app.sources.connectors.eurostat_comext import (BASE_URL, DATASET, IMPORTS, EXPORTS,
                                                    MARKET_QUANTITY_TONNES, QUANTITY,
                                                    VALUE, PUBLICATION_DAY, PUBLICATION_LAG_MONTHS,
                                                    EurostatComextConnector, _add_months,
                                                    _updated_stamp)
from app.trade import materials as m
from app.trade.jsonstat import cells, dimension_labels

EXTRA_EU = "EXT_EU27_2020"
EXTRA_EU_LABEL = "Extra-EU27"
PRIMARY_SOURCE = "Eurostat Comext"
SOURCE_TYPE = "Official trade statistics"
NOMENCLATURE = "Combined Nomenclature (CN8)"

# Names for the partners this layer is configured with. Any other partner is named by Eurostat's own
# label from the response, or by its code when the response carries none.
PARTNER_NAMES = {"CN": "China", "US": "United States", "KR": "South Korea", "SA": "Saudi Arabia",
                 "IN": "India", "TR": "Türkiye", "JP": "Japan", "GB": "United Kingdom"}

# A month with a value and a quantity has a unit value, however small. Whether that unit value is
# a market PRICE is a second question, and since v0.5.46 it is answered by MARKET_QUANTITY_TONNES
# in app.sources.connectors.eurostat_comext - 100 t in a month, measured over 5,811 months against
# each month's own opposite leg. The line here used to read "No minimum volume" (Stefan,
# 13 October 2026: "I disagree with any gate that has a minimum value"), and under it the EU's
# 82 kg of dimethyl terephthalate exports in August 2010 became a price of 230,902 EUR/t. Stefan's
# decision of 17 October on UN Comtrade settled the principle: the tonnage is kept and so is the
# unit value, as a feature record; only the claim that it is a price is withdrawn.
EU27 = frozenset({"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE",
                  "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"})


def _key(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def _month_label(period: str) -> str | None:
    moment = EurostatComextConnector._month(period)
    return f"{moment:%Y-%m}" if moment else None


class EurostatTradeConnector:
    allow_empty_parse = False

    def __init__(self, source_id: str, material: m.Material, *, predecessor: bool = False):
        self.source_id = source_id
        self.material = material
        self.predecessor = predecessor
        if predecessor:
            spec = material.eurostat_predecessor
            self.cn_code, self.label = spec.cn, spec.label
            self.mapping_status, self.mapping_note = spec.mapping_status, spec.note
            self.grade = f"CN {spec.cn}"
            self.partners = material.import_partners
            self.breakdown = False
        else:
            self.cn_code, self.label = material.cn, material.eurostat_label
            self.mapping_status = "current code"
            self.mapping_note = ""
            self.grade = None
            self.partners = material.import_partners
            self.breakdown = material.supplier_breakdown
        self.product = material.product
        self.prefix = "eu_polyol" if material.key == "polyether_polyol" else f"eu_{material.key}"
        if predecessor:
            self.prefix += f"_cn{self.cn_code}"
        self.source_url = self._url(IMPORTS, EXTRA_EU, VALUE)

    # -- fetching --------------------------------------------------------------------------------

    def _url(self, flow: str, partner: str | None, indicator: str) -> str:
        partner_part = f"&partner={partner}" if partner else ""
        return (f"{BASE_URL}/{DATASET}?format=JSON&lang=EN&freq=M&reporter=EU27_2020{partner_part}"
                f"&product={self.cn_code}&flow={flow}&indicators={indicator}")

    def requests(self) -> list[tuple[str, str, str | None, str]]:
        """(name, flow, partner, indicator) for every request one run makes."""
        out = []
        for flow in (IMPORTS, EXPORTS):
            for indicator in (VALUE, QUANTITY):
                out.append((f"{flow}|{EXTRA_EU}|{indicator}", flow, EXTRA_EU, indicator))
        for partner in self.partners:
            for indicator in (VALUE, QUANTITY):
                out.append((f"{IMPORTS}|{partner}|{indicator}", IMPORTS, partner, indicator))
        if self.breakdown:
            for indicator in (VALUE, QUANTITY):
                out.append((f"breakdown|{indicator}", IMPORTS, None, indicator))
        return out

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    def fetch_item(self, item: SourceItem) -> bytes:
        cubes = {}
        for name, flow, partner, indicator in self.requests():
            response = httpx.get(self._url(flow, partner, indicator), timeout=90.0,
                                 headers={"Accept": "application/json"})
            response.raise_for_status()
            cubes[name] = response.json()
        return json.dumps({"material": self.material.key, "cn": self.cn_code, "cubes": cubes}).encode("utf-8")

    # -- reading ---------------------------------------------------------------------------------

    @staticmethod
    def _monthly(cube: dict | None) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, value in cells(cube or {}).items():
            month = _month_label(key[-1]) if key else None
            if month:
                out[month] = value
        return out

    def _publication(self, month: str, newest: str, updated: datetime | None) -> tuple[datetime, str]:
        year, number = int(month[:4]), int(month[5:])
        estimated = _add_months(datetime(year, number, 1, tzinfo=timezone.utc),
                                PUBLICATION_LAG_MONTHS).replace(day=PUBLICATION_DAY)
        # The update stamp dates only the month it published. A closed predecessor code, or a series
        # whose newest month is older than the release it arrived in, gets the estimate instead.
        if (updated is not None and month == newest and not self.predecessor
                and _add_months(datetime(year, number, 1, tzinfo=timezone.utc), 3) > updated):
            return updated, "measured: Eurostat dataset update stamp"
        if updated is not None and estimated > updated:
            return updated, "estimated, capped at the Eurostat dataset update stamp"
        return estimated, "estimated: observation month + 2 months, 15th"

    @staticmethod
    def _row(value: float | None, hundred_kg: float | None) -> dict:
        """One month, with the two questions kept apart.

        `has_quantity` asks whether anything moved that can be divided into - whether the tonnage
        is a fact and a unit value exists at all. `priceable` asks whether it was a MARKET
        quantity - whether that unit value is a price. They are different questions and were run
        together until v0.5.46, which is how 82 kg of dimethyl terephthalate became 230,902 EUR/t.
        """
        tonnes = hundred_kg / 10.0 if hundred_kg is not None else None
        has_quantity = value is not None and value > 0 and tonnes is not None and tonnes > 0
        priceable = has_quantity and tonnes >= MARKET_QUANTITY_TONNES
        return {"value_eur": value, "quantity_100kg": hundred_kg, "tonnes": tonnes,
                "eur_per_tonne": (value / tonnes) if has_quantity else None,
                "has_quantity": has_quantity, "priceable": priceable,
                "status": None if priceable
                          else m.MISSING_QUANTITY if not has_quantity
                          else m.BELOW_MARKET_QUANTITY}

    @classmethod
    def _rows(cls, values: dict[str, float], quantities: dict[str, float]) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        for month in sorted(set(values) | set(quantities)):
            rows[month] = cls._row(values.get(month), quantities.get(month))
        return rows

    def series(self, payload: str) -> dict:
        document = json.loads(payload)
        cubes = document["cubes"]
        out: dict = {"series": {}, "updated": None, "label_seen": None, "breakdown": None}
        stamps = [s for s in (_updated_stamp(c) for c in cubes.values()) if s]
        out["updated"] = max(stamps) if stamps else None
        first = cubes.get(f"{IMPORTS}|{EXTRA_EU}|{VALUE}") or {}
        labels = dimension_labels(first, "product")
        out["label_seen"] = labels.get(self.cn_code) or (next(iter(labels.values())) if labels else None)

        def pair(flow, partner):
            return self._rows(self._monthly(cubes.get(f"{flow}|{partner}|{VALUE}")),
                              self._monthly(cubes.get(f"{flow}|{partner}|{QUANTITY}")))

        series = out["series"]
        series["import|extra_eu"] = {"flow": "Import", "partner": EXTRA_EU_LABEL, "partner_code": EXTRA_EU,
                                     "type": m.IMPORT_UNIT_VALUE, "rows": pair(IMPORTS, EXTRA_EU)}
        series["export|extra_eu"] = {"flow": "Export", "partner": EXTRA_EU_LABEL, "partner_code": EXTRA_EU,
                                     "type": m.EXPORT_UNIT_VALUE, "rows": pair(EXPORTS, EXTRA_EU)}
        total = series["import|extra_eu"]["rows"]
        for partner in self.partners:
            name = PARTNER_NAMES.get(partner, partner)
            rows = pair(IMPORTS, partner)
            series[f"import|{_key(name)}"] = {"flow": "Import", "partner": name, "partner_code": partner,
                                              "type": m.IMPORT_UNIT_VALUE, "rows": rows}
            residual: dict[str, dict] = {}
            for month, t in total.items():
                c = rows.get(month)
                if not c or t["value_eur"] is None or c["value_eur"] is None \
                        or t["quantity_100kg"] is None or c["quantity_100kg"] is None:
                    continue
                residual[month] = self._row(t["value_eur"] - c["value_eur"],
                                            t["quantity_100kg"] - c["quantity_100kg"])
            other = f"{EXTRA_EU_LABEL} excluding {name}"
            series["import|other"] = {"flow": "Import", "partner": other, "partner_code": f"{EXTRA_EU}-{partner}",
                                      "type": m.DERIVED_UNIT_VALUE, "rows": residual, "derived": True}
        if self.breakdown and f"breakdown|{QUANTITY}" in cubes:
            out["breakdown"] = self._top_three(cubes[f"breakdown|{QUANTITY}"], total)
        return out

    @staticmethod
    def _top_three(cube: dict, total: dict[str, dict]) -> dict[str, dict]:
        """Share of extra-EU import tonnes held by the three largest supplying countries, monthly.

        Country partners outside the EU only. The all-partner cube for reporter EU27 also lists the
        member states (intra-EU trade), the confidential 'Q' groupings and aggregates such as
        EXT_EU27_2020 and WORLD; the first two are excluded by name, the aggregates by code length.
        Checked 13 October 2026: July 2026 extra-EU countries summed to 18,658 t against Eurostat's
        own extra-EU total of 18,655 t.
        """
        ids = list(cube.get("id") or [])
        if "partner" not in ids:
            return {}
        by_month: dict[str, dict[str, float]] = {}
        partner_pos = [i for i, name in enumerate(ids) if (cube.get("size") or [])[i] > 1].index(ids.index("partner")) \
            if (cube.get("size") or [1])[ids.index("partner")] > 1 else None
        if partner_pos is None:
            return {}
        for key, hundred_kg in cells(cube).items():
            partner, period = key[partner_pos], key[-1]
            month = _month_label(period)
            if not month or len(partner) != 2 or partner.startswith("Q") or partner in EU27 \
                    or partner in {"EU", "EA"}:
                continue
            by_month.setdefault(month, {})[partner] = hundred_kg / 10.0
        labels = dimension_labels(cube, "partner")
        out: dict[str, dict] = {}
        for month, partners in by_month.items():
            whole = (total.get(month) or {}).get("tonnes")
            if not whole:
                continue
            top = sorted(partners.items(), key=lambda kv: kv[1], reverse=True)[:3]
            out[month] = {"share": sum(t for _, t in top) / whole,
                          "suppliers": [PARTNER_NAMES.get(code) or labels.get(code) or code for code, _ in top],
                          "supplier_codes": [code for code, _ in top],
                          "supplier_tonnes": [round(t, 3) for _, t in top]}
        return out

    # -- records ---------------------------------------------------------------------------------

    def extract_html(self, payload: str, source_url: str | None = None) -> list[ExtractedRecord]:
        found = self.series(payload)
        updated = found["updated"]
        url = source_url or self.source_url
        months = sorted({mo for s in found["series"].values() for mo in s["rows"]})
        if not months:
            return []
        newest = months[-1]
        retrieved = datetime.now(timezone.utc).isoformat()
        label_seen = found["label_seen"]
        statuses: list[str] = []
        if label_seen and label_seen != self.label:
            statuses.append(m.CLASSIFICATION_CHANGE)
        if self.predecessor:
            statuses.append(m.HISTORICAL_MAPPING_REQUIRED)
        common = {
            "material": self.material.key,
            "cn_code": self.cn_code,
            "hs_code": self.material.hs if not self.predecessor else None,
            "nomenclature": NOMENCLATURE,
            "product_description": label_seen or self.label,
            "expected_description": self.label,
            "predecessor_mapping_status": self.mapping_status,
            "mapping_note": self.mapping_note or None,
            "reporter": "EU27_2020",
            "primary_source": PRIMARY_SOURCE,
            "source_dataset": DATASET,
            "source_type": SOURCE_TYPE,
            "frequency": "Monthly",
            "eurostat_updated": updated.isoformat() if updated else None,
            "source_updated": updated.isoformat() if updated else None,
            "retrieved_at": retrieved,
            "limitation_note": self.material.limitation or None,
            "refresh_statuses": statuses,
            "record_revisions": True,
        }
        out: list[ExtractedRecord] = []
        for series_key, spec in found["series"].items():
            flow_word, series_name = series_key.split("|")
            rows = spec["rows"]
            derived = bool(spec.get("derived"))
            basis_label = m.data_basis(spec["type"])
            for month, row in rows.items():
                observed = datetime(int(month[:4]), int(month[5:]), 1, tzinfo=timezone.utc)
                published, publication_basis = self._publication(month, newest, updated)
                if derived:
                    method = (f"(Extra-EU value - {spec['partner'].split('excluding ')[-1]} value) / "
                              f"((Extra-EU quantity - partner quantity) x 0.1)")
                else:
                    method = "VALUE_IN_EUROS / (QUANTITY_IN_100KG x 0.1)"
                record = {
                    **common,
                    "flow": flow_word.capitalize(),
                    "partner": spec["partner"],
                    "partner_code": spec["partner_code"],
                    "observation_month": month,
                    "observation_date": observed,
                    "value_eur": row["value_eur"],
                    "trade_value": row["value_eur"],
                    "currency": "EUR",
                    "quantity_100kg": row["quantity_100kg"],
                    "quantity": row["quantity_100kg"],
                    "quantity_unit": "100 kg",
                    "net_weight": row["tonnes"],
                    "net_weight_unit": "t",
                    "tonnes": round(row["tonnes"], 3) if row["tonnes"] is not None else None,
                    "unit_value": round(row["eur_per_tonne"], 2) if row["eur_per_tonne"] is not None else m.NO_RELIABLE_UNIT_VALUE,
                    "unit_value_currency": "EUR",
                    "unit_value_basis": "per tonne",
                    "data_basis": basis_label,
                    "basis_label": basis_label,
                    "calculation_method": method,
                    "derived_residual": derived,
                    "derivation_note": "Derived residual from Eurostat data" if derived else "Published Eurostat series",
                    "publication_date_basis": publication_basis,
                    "observation_status": row["status"],
                    "raw_signature": f"{row['value_eur']}|{row['quantity_100kg']}",
                }
                # The record says which fault it has and names the tonnage, so a reader of the
                # feature can see exactly why no price was drawn from it.
                if row["has_quantity"] and not row["priceable"]:
                    record["limitation_note"] = " ".join(filter(None, [
                        self.material.limitation or "",
                        f"{row['tonnes']:,.3f} t moved in {month}, under the "
                        f"{MARKET_QUANTITY_TONNES:,.0f} t a monthly unit value needs to be a "
                        "market price. The tonnage is kept; the unit value is not stored as a "
                        "price."])).strip()
                # The market price is the extra-EU total, imports and exports. China and the other
                # origins are the same month of the same market split by origin; drawn beside the
                # total they would be three datapoints for one market and month (Stefan, 13 October
                # 2026: make sure no double datapoints are made). They are kept as variables.
                if row["priceable"] and spec["partner_code"] == EXTRA_EU:
                    flow_is_import = flow_word == "import"
                    origin = None
                    out.append(ExtractedRecord(
                        kind="price", source_url=url, publication_date=published,
                        data={**record, "type": "price", "product": self.product, "grade": None,
                              "market_geography": "Europe",
                              "origin": origin,
                              "commercial_basis": "CIF" if flow_is_import else "FOB",
                              "price": round(row["eur_per_tonne"], 2), "unit": "t",
                              # A declared trade, so a deal - see `price_kinds`.
                              "observation_type": price_kinds.DEAL,
                              "source_label": (f"EU {self.product} {'Import' if flow_is_import else 'Export'} "
                                               f"Unit Value - {spec['partner']} (CN {self.cn_code}), "
                                               f"{observed:%B %Y}: EUR {row['value_eur']:,.0f} for "
                                               f"{row['tonnes']:,.1f} t")}))
                out.append(ExtractedRecord(
                    kind="feature", source_url=url, publication_date=published,
                    data={**record, "type": "feature", "product": self.product,
                          "feature_type": f"{self.prefix}_{flow_word}_record_{series_name}",
                          "value": round(row["eur_per_tonne"], 2) if row["eur_per_tonne"] is not None else None}))
        out.extend(self._variables(found, newest, updated, url, common))
        return out

    def _variables(self, found, newest, updated, url, common) -> list[ExtractedRecord]:
        p = self.prefix
        series = found["series"]
        variables: dict[str, tuple[str, dict[str, float]]] = {}

        def tonnes(key):
            return {mo: r["tonnes"] for mo, r in series[key]["rows"].items() if r["tonnes"] is not None}

        def unit_values(key):
            return {mo: r["eur_per_tonne"] for mo, r in series[key]["rows"].items() if r["eur_per_tonne"] is not None}

        imports, exports = tonnes("import|extra_eu"), tonnes("export|extra_eu")
        variables[f"{p}_import_tonnes_extra_eu"] = (m.CUSTOMS_TRADE_VOLUME, imports)
        variables[f"{p}_export_tonnes_extra_eu"] = (m.CUSTOMS_TRADE_VOLUME, exports)
        variables[f"{p}_net_import_tonnes"] = (m.DERIVED_TRADE_INDICATOR,
                                              {mo: imports[mo] - exports[mo] for mo in imports if mo in exports})
        for key, spec in series.items():
            flow_word, name = key.split("|")
            if flow_word == "import" and name not in ("extra_eu", "other"):
                partner_tonnes = tonnes(key)
                variables[f"{p}_import_tonnes_{name}"] = (m.CUSTOMS_TRADE_VOLUME, partner_tonnes)
                share = {mo: t / imports[mo] for mo, t in partner_tonnes.items() if imports.get(mo)}
                variables[f"{p}_import_{name}_share"] = (m.DERIVED_TRADE_INDICATOR, share)
                variables[f"{p}_import_{name}_share_change"] = (m.DERIVED_TRADE_INDICATOR,
                                                                self._difference(share, 1))
        if found.get("breakdown"):
            variables[f"{p}_import_top3_supplier_share"] = (
                m.DERIVED_TRADE_INDICATOR, {mo: v["share"] for mo, v in found["breakdown"].items()})
        for key in series:
            flow_word, name = key.split("|")
            vol = tonnes(key)
            uv = unit_values(key)
            for label, lag in (("mom", 1), ("yoy", 12)):
                if name != "other":
                    variables[f"{p}_{flow_word}_tonnes_{name}_{label}"] = (m.DERIVED_TRADE_INDICATOR,
                                                                          self._change(vol, lag))
                variables[f"{p}_{flow_word}_unit_value_{name}_{label}"] = (m.DERIVED_TRADE_INDICATOR,
                                                                          self._change(uv, lag))
        out: list[ExtractedRecord] = []
        breakdown = found.get("breakdown") or {}
        for feature_type, (basis, values) in variables.items():
            for month, value in sorted(values.items()):
                published, publication_basis = self._publication(month, newest, updated)
                data = {**common, "type": "feature", "feature_type": feature_type, "product": self.product,
                        "observation_month": month, "value": round(value, 6), "data_basis": basis,
                        "publication_date_basis": publication_basis,
                        "calculation_method": self._method(feature_type),
                        "raw_signature": f"{round(value, 6)}"}
                if feature_type.endswith("top3_supplier_share") and month in breakdown:
                    data["suppliers"] = breakdown[month]["suppliers"]
                    data["supplier_tonnes"] = breakdown[month]["supplier_tonnes"]
                out.append(ExtractedRecord(kind="feature", source_url=url, publication_date=published, data=data))
        return out

    @staticmethod
    def _method(feature_type: str) -> str:
        if feature_type.endswith("_mom"):
            return "value / value one month earlier - 1; not bridged across a missing month"
        if feature_type.endswith("_yoy"):
            return "value / value twelve months earlier - 1; not bridged across a missing month"
        if feature_type.endswith("_share_change"):
            return "share minus share one month earlier, in share points"
        if feature_type.endswith("_share"):
            return "partner tonnes / extra-EU tonnes, same month"
        if feature_type.endswith("_net_import_tonnes"):
            return "extra-EU import tonnes - extra-EU export tonnes, same month"
        return "QUANTITY_IN_100KG x 0.1"

    @staticmethod
    def _shift(month: str, lag: int) -> str:
        moment = _add_months(datetime(int(month[:4]), int(month[5:]), 1, tzinfo=timezone.utc), -lag)
        return f"{moment:%Y-%m}"

    @classmethod
    def _change(cls, series: dict[str, float], lag: int) -> dict[str, float]:
        out = {}
        for month, value in series.items():
            before = series.get(cls._shift(month, lag))
            if before:
                out[month] = value / before - 1.0
        return out

    @classmethod
    def _difference(cls, series: dict[str, float], lag: int) -> dict[str, float]:
        out = {}
        for month, value in series.items():
            before = series.get(cls._shift(month, lag))
            if before is not None:
                out[month] = value - before
        return out
