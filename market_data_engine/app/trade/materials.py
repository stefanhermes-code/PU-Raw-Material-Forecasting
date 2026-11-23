"""The public trade-data layer: which materials, under which customs codes, and what each number is.

Stefan, 13 October 2026, "JC Instruction - Integrate Eurostat + UN Comtrade Raw-Material Data Layer".
Two layers, kept structurally distinct and available together:

  EU trade layer      Eurostat Comext DS-045409, CN8, EU27, monthly, EUR.
  Global trade layer  UN Comtrade, HS6, reporter countries, monthly and annual, USD.

Eurostat is authoritative for EU27 trade; Comtrade's EU figures are never used in its place. The two
are never spliced into one series: every reading keeps its source, code, nomenclature and frequency.

The figures are declared commercial-invoice values and are stored as market prices, with no type of
their own (Stefan, 13 October 2026). No market gets two datapoints for one period. And a declaration
is only a price above a market quantity, measured per lane: 1,000 t for a Comtrade row, 100 t for a
Comext month. The line here read "No minimum volume gates them" until v0.5.46; under it, 82 kg of
dimethyl terephthalate leaving the EU in August 2010 was published as 230,902 EUR/t.

Codes checked against Eurostat's own product labels on 13 October 2026. Two departures from the
instruction's table, both on evidence:

  Benzene and toluene use 29022000 / 29023000 (HS 290220 / 290230), the chemical-grade codes this
  application already collected. The instruction listed 27071000 / 27072000, which Eurostat labels
  coal-tar "benzol" and "toluol": EU imports of toluol averaged about 1 t a month over the last
  year against about 10,000 t under 29023000.

  Polyether polyol's predecessor CN 39072020 (2010-2021) continues the same market line, collected
  by its own source and flagged on every record; the checks below were done before joining. Its label is "Polyether alcohols, in primary forms (excl. polyethylene
  glycols)"; the current 39072920 also excludes bis(polyoxyethylene) methylphosphonate, which moved
  to 39072100 and carried 479 t of extra-EU imports in 2023 and 1,663 t in 2025 against roughly
  200,000 t a year of polyether alcohols. Unit values carry on across the break (EUR 3,372/t in
  December 2021, 3,016 in January 2022). Mapping status: validated predecessor, scope differs by
  under one percent.
"""
from __future__ import annotations

from dataclasses import dataclass
from raw_material_forecasting import price_kinds

# --- Price / Data Basis ------------------------------------------------------------------------
#
# A descriptive label each trade record carries (Stefan's instruction, section 13). It labels; it
# gates nothing. The trade layer's prices are stored as ordinary market prices.

MARKET_PRICE_ASSESSMENT = "Market Price Assessment"
SPOT_PRICE = "Spot Price"
CONTRACT_PRICE = "Contract Price"
PRODUCER_PRICE_ANNOUNCEMENT = "Producer Price Announcement"
CUSTOMS_IMPORT_UNIT_VALUE = "Customs Import Unit Value"
CUSTOMS_EXPORT_UNIT_VALUE = "Customs Export Unit Value"
CUSTOMS_TRADE_VOLUME = "Customs Trade Volume"
DERIVED_TRADE_INDICATOR = "Derived Trade Indicator"
OTHER = "Other"

DATA_BASES = (MARKET_PRICE_ASSESSMENT, SPOT_PRICE, CONTRACT_PRICE, PRODUCER_PRICE_ANNOUNCEMENT,
              CUSTOMS_IMPORT_UNIT_VALUE, CUSTOMS_EXPORT_UNIT_VALUE, CUSTOMS_TRADE_VOLUME,
              DERIVED_TRADE_INDICATOR, OTHER)

# Observation types the trade layer writes. None of them is in the customer page's published list.
IMPORT_UNIT_VALUE = "trade_unit_value"                      # monthly import unit value
EXPORT_UNIT_VALUE = "trade_export_unit_value"               # monthly export unit value
DERIVED_UNIT_VALUE = "derived_trade_unit_value"             # a residual HTC computes
IMPORT_UNIT_VALUE_ANNUAL = "trade_unit_value_annual"        # annual import unit value
EXPORT_UNIT_VALUE_ANNUAL = "trade_export_unit_value_annual" # annual export unit value

TRADE_OBSERVATION_TYPES = frozenset({IMPORT_UNIT_VALUE, EXPORT_UNIT_VALUE, DERIVED_UNIT_VALUE,
                                     IMPORT_UNIT_VALUE_ANNUAL, EXPORT_UNIT_VALUE_ANNUAL})

# Keyed on the four kinds of price (`raw_material_forecasting.price_kinds`) plus the trade layer's
# own types. It was keyed on four of the old seven, two of which - `spot_price` and `producer_price`
# - no identity in the store ever held, and after migration 0058 none of the four keys existed at
# all, so every real reading would have been labelled "Other".
#
# `SPOT_PRICE` keeps its label for the assessed kind's sibling use and `deal` takes the customs
# label it already had, because a deal with no customs lane behind it is still a recorded trade.
_BASIS_BY_TYPE = {
    price_kinds.ASSESSED: MARKET_PRICE_ASSESSMENT,
    price_kinds.CONTRACT: CONTRACT_PRICE,
    price_kinds.DEAL: SPOT_PRICE,
    price_kinds.ASK: PRODUCER_PRICE_ANNOUNCEMENT,
    IMPORT_UNIT_VALUE: CUSTOMS_IMPORT_UNIT_VALUE,
    IMPORT_UNIT_VALUE_ANNUAL: CUSTOMS_IMPORT_UNIT_VALUE,
    EXPORT_UNIT_VALUE: CUSTOMS_EXPORT_UNIT_VALUE,
    EXPORT_UNIT_VALUE_ANNUAL: CUSTOMS_EXPORT_UNIT_VALUE,
    DERIVED_UNIT_VALUE: DERIVED_TRADE_INDICATOR,
}


def data_basis(observation_type: str | None) -> str:
    """The Price / Data Basis for an observation type. Anything not named is Other, never a price."""
    return _BASIS_BY_TYPE.get(str(observation_type or price_kinds.HEADLINE), OTHER)


# --- Refresh status ----------------------------------------------------------------------------

NEW_OBSERVATION = "New observation"
REVISED_OBSERVATION = "Revised observation"
NO_NEW_OBSERVATION = "No new observation"
MISSING_QUANTITY = "Missing quantity / no unit value"
# The declaration carries a weight, and it is too small for the division to mean a market
# price. Two floors, each measured on its own population and neither borrowed from the other:
# MARKET_QUANTITY_TONNES in app.trade.comtrade (1,000 t, one country's declaration under an HS6
# code) and in app.sources.connectors.eurostat_comext (100 t in a month, the whole EU27 under one
# CN8 code).
BELOW_MARKET_QUANTITY = "Below a market quantity / unit value withheld"
SOURCE_UNAVAILABLE = "Source unavailable"
API_ERROR = "API error"
CLASSIFICATION_CHANGE = "Classification change detected"
HISTORICAL_MAPPING_REQUIRED = "Historical mapping required"

NO_RELIABLE_UNIT_VALUE = "n/r"


# --- Materials ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Predecessor:
    """An older customs code for the same material, held as its own series."""
    cn: str | None
    hs: str | None
    label: str
    valid_to: str                 # last period the code was in force, "YYYY-MM" or "YYYY"
    mapping_status: str
    note: str


@dataclass(frozen=True)
class Material:
    key: str                      # feature prefix and source-id stem
    product: str                  # what the identity resolver reads
    cn: str                       # Eurostat CN8
    hs: str                       # UN Comtrade HS6
    eurostat_label: str           # Eurostat's product label, checked each run
    import_partners: tuple[str, ...] = ()    # Eurostat partner series beyond the extra-EU total
    supplier_breakdown: bool = False         # fetch all partners to measure top-three supplier share
    eurostat_predecessor: Predecessor | None = None
    hs_predecessor: Predecessor | None = None
    limitation: str = ""
    legacy_source_id: str | None = None      # the Comext source id this material was collected under
    forecast_targets: tuple[str, ...] = ()   # candidate relationships; not assumed predictive


POLYETHER_PREDECESSOR_NOTE = (
    "CN 39072020 'Polyether alcohols, in primary forms (excl. polyethylene glycols)' was in force to "
    "December 2021. From January 2022 bis(polyoxyethylene) methylphosphonate has its own code "
    "(39072100: 479 t of extra-EU imports in 2023, 1,663 t in 2025) and the rest is 39072920. "
    "Descriptions compared, scope overlap confirmed, transition flagged; held as a separate series.")

MATERIALS: tuple[Material, ...] = (
    Material("polyether_polyol", "Polyether Polyol", "39072920", "390729",
             "Polyether alcohols, in primary forms (excl. bis(polyoxyethylene) methylphosphonate and polyethylene glycols)",
             import_partners=("CN",), supplier_breakdown=True,
             eurostat_predecessor=Predecessor(
                 "39072020", None, "Polyether alcohols, in primary forms (excl. polyethylene glycols)",
                 "2021-12", "joined: validated predecessor, scope differs by under 1%, code changed January 2022", POLYETHER_PREDECESSOR_NOTE),
             hs_predecessor=Predecessor(
                 None, "390720", "Other polyethers, in primary forms (HS 2017 subheading 3907.20)",
                 "2021", "joined: validated predecessor, HS 2022 split off 390721 (under 1% of volume), code changed 2022",
                 "HS 390720 (HS 2017) became 390721 + 390729 in HS 2022. 390721 is "
                 "bis(polyoxyethylene) methylphosphonate. Reproduces PetroApex: China exports "
                 "815,752 t at USD 1,781/t in 2020 under 390720."),
             limitation=("CN 39072920 holds every polyether alcohol in primary form, not one commercial "
                         "grade; HS 390729 is broader still and includes polyethylene glycols."),
             legacy_source_id="comext_polyether_polyol_eu",
             forecast_targets=("POLYETHER_POLYOL",)),
    Material("propylene_oxide", "Propylene oxide", "29102000", "291020", 'Methyloxirane "propylene oxide"',
             legacy_source_id="comext_propylene_oxide_eu", forecast_targets=("POLYETHER_POLYOL",),
             limitation="Europe largely makes and uses its own PO; traded volume is a tail of the market."),
    Material("ethylene_oxide", "Ethylene oxide", "29101000", "291010", 'Oxirane "ethylene oxide"',
             forecast_targets=("POLYETHER_POLYOL",),
             limitation="Extra-EU imports run at a few tonnes a month: the import unit value is n/r."),
    Material("diethylene_glycol", "Diethylene glycol", "29094100", "290941",
             "2,2'-Oxydiethanol \"diethylene glycol, digol\"",
             legacy_source_id="comext_diethylene_glycol_eu", forecast_targets=("POLYESTER_POLYOL",)),
    Material("ethylene_glycol", "Ethylene glycol", "29053100", "290531", 'Ethylene glycol "ethanediol"',
             forecast_targets=("POLYESTER_POLYOL",)),
    Material("phthalic_anhydride", "Phthalic anhydride", "29173500", "291735", "Phthalic anhydride",
             forecast_targets=("POLYESTER_POLYOL",)),
    Material("maleic_anhydride", "Maleic anhydride", "29171400", "291714", "Maleic anhydride",
             forecast_targets=("POLYESTER_POLYOL",)),
    Material("terephthalic_acid", "Terephthalic acid", "29173600", "291736", "Terephthalic acid and its salts",
             forecast_targets=("POLYESTER_POLYOL",),
             limitation="The code includes the salts of terephthalic acid."),
    Material("dimethyl_terephthalate", "Dimethyl terephthalate", "29173700", "291737", "Dimethyl terephthalate",
             forecast_targets=("POLYESTER_POLYOL",),
             limitation="Extra-EU exports run at about a tonne a month: the export unit value is n/r."),
    Material("toluene", "Toluene", "29023000", "290230", "Toluene",
             legacy_source_id="comext_toluene_eu", forecast_targets=("TDI",)),
    Material("benzene", "Benzene", "29022000", "290220", "Benzene",
             legacy_source_id="comext_benzene_eu", forecast_targets=("PMDI", "PURE_MDI")),
    # Not in the 13 October list; collected since 1 October 2026 on Stefan's earlier instruction and
    # kept so its history does not stop.
    Material("adipic_acid", "Adipic acid", "29171200", "291712", "Adipic acid, its salts and esters",
             legacy_source_id="comext_adipic_acid_eu", forecast_targets=("POLYESTER_POLYOL",),
             limitation="The code includes esters, which are plasticisers and dilute the unit value."),
)

BY_KEY = {m.key: m for m in MATERIALS}


def eurostat_source_id(material: Material) -> str:
    return material.legacy_source_id or f"comext_{material.key}_eu"


def eurostat_predecessor_source_id(material: Material) -> str:
    return f"comext_{material.key}_eu_cn{material.eurostat_predecessor.cn}"


# --- UN Comtrade reporters ---------------------------------------------------------------------

REPORTERS: dict[str, str] = {
    "156": "China",
    "842": "United States",
    "410": "South Korea",
    "682": "Saudi Arabia",
    "699": "India",
    "792": "Türkiye",
    "392": "Japan",
}

# Bilateral flows into the EU, by member state: Comtrade has no EU partner aggregate, and the EU
# side of these flows belongs to Eurostat anyway. Measured on 13 October 2026 for polyether polyol,
# 2024 exports: China shipped 4,500-41,000 t to each of Belgium, Germany, Italy, the Netherlands and
# Poland; Korea 1,500-19,000 t to each; Saudi Arabia 30,785 t to Belgium; the US 37,000 t to Belgium
# and 3 t to Poland. Series below the floor further down are not kept.
BILATERAL_REPORTERS = ("156", "410", "682", "842")
EU_PARTNERS: dict[str, str] = {
    "56": "Belgium", "276": "Germany", "380": "Italy", "528": "Netherlands", "616": "Poland",
    "724": "Spain", "251": "France",
}
BILATERAL_MATERIALS = ("polyether_polyol",)
