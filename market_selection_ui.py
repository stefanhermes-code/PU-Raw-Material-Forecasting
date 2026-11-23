from __future__ import annotations

from raw_material_forecasting.chemicals import canonical_code, display_chemical
from raw_material_forecasting import identity_vocabulary, market_basis, price_kinds
from raw_material_forecasting.series_curation import forced_onto_pages

# The store's own codes, one per material. Not display names: the page renders them through
# `display_chemical` at the moment it draws them, so the selector and the series can never drift
# apart. This list held "TDI" and "PMDI" as codes and the polyols as display names at the same time,
# which is how two different series came to read "Polyether Polyol" on one dropdown.
#
# The polyols are named one by one rather than as "Polyol". They are different materials with
# different feedstocks, different foams and different prices, and a buyer of HR polyol is not
# shopping for polyester. Stefan, 17 September 2026.
# One list, and it lives in `identity_vocabulary` (masterplan D, v1.2.11). It was here, at the
# repository root, which the engine cannot import - so the engine had no way to ask "is this a
# chemical we price?" at the door, and `is_offered_chemical` below asked it on the way OUT, where
# the market had already been minted. Moved into `src/`, which both sides import.
CHEMICALS = identity_vocabulary.RAW_MATERIALS

# Chemicals switched on for market-data work. Others stay registered and collected where a
# connector exists, but are hidden from coverage and status views until switched on here.
#
# PMDI joined on 16 September 2026, once its series was one series: three identities merged into
# one, a producer's fixed-price announcement taken back out of it, and the SunSirs research history
# converted at each reading's own ECB cross and joined on. Two publishers print it daily and SCI99
# is wired as the third.
#
# Polyether polyol the same day: five identities into one, the two publishers agreeing to within 1%
# once ECHEMI was read off its own table, and FoamPartner's Changzhou history and KGI's Shell
# Caradol purchases joined on.
# Every PU raw material we hold a price for.
#
# This was three - TDI, pMDI and polyether polyol - on the reasoning that a page should not offer a
# market it has no price for. After the ICIS import that reasoning inverted: the store holds 2,106
# readings of pure MDI, 732 of rigid polyol, 577 of HR polyol and 371 of graft polyol, none of which
# reached a screen. Stefan, 26 September 2026: "The more chemicals we can add that are PU Raw
# Materials the better."
#
# Feedstocks stay off. Toluene, benzene, aniline, EO, DEG and adipic acid are what these are made
# from, and a foam producer does not buy them. They remain visible to HTC on Platform Admin.
# The same list, and the same object. Two names were kept and a comment above claimed CHEMICALS was
# "the wider roster", but the two tuples have been identical since 26 September 2026 when the roster
# opened - so the claim was a third thing to keep in step with nothing keeping it. One object until
# somebody has a reason for two; `tests/test_nothing_mints_a_market_unseen.py` says so out loud.
ACTIVE_CHEMICALS = CHEMICALS
# What a chemical IS, for the one page that shows both kinds side by side.
#
# Stefan, 1 October 2026, looking at Data & Sources: "we mix the PU RM chemicals and the feedstock
# chemicals in one overview. I think that is not right." A foam producer buys the raw materials and
# does not buy the feedstocks; the feedstocks are collected because they explain the raw materials.
# The customer pages already carry only ACTIVE_CHEMICALS; this names the other half so HTC's own
# page can say which is which in a column rather than leaving the reader to know.
FEEDSTOCK_CHEMICALS = identity_vocabulary.FEEDSTOCKS


# Which feedstock sits under which raw material, and how far under it.
#
# Stefan, 1 October 2026, agreed the feedstocks leave the customer-facing chemical list and reappear
# beneath the market they explain: boxes under the chart, not a page of their own. "A foam producer
# does not buy them" - what they want to know is which way the pressure under their material is
# moving. This mirrors the route graph the engine already holds; it is repeated here because the
# engine's package cannot be imported by the application.
FEEDSTOCK_OF: dict[str, tuple[tuple[str, str], ...]] = {
    "TDI": (("TOLUENE", ""),),
    "PMDI": (("BENZENE", "via aniline"),),
    "PURE_MDI": (("BENZENE", "via aniline"),),
    "POLYETHER_POLYOL": (("PO", ""), ("EO", "")),
    "RIGID_POLYOL": (("PO", ""),),
    "HR_POLYOL": (("PO", ""),),
    "GRAFT_POLYOL": (("PO", ""),),
    "PHD_POLYOL": (("PO", ""),),
    "PIPA_POLYOL": (("PO", ""),),
    "VISCO_POLYOL": (("PO", ""),),
    "ALIPHATIC_POLYESTER_POLYOL": (("ADIPIC_ACID", ""), ("DIETHYLENE_GLYCOL", ""),
                                   ("ETHYLENE_GLYCOL", ""), ("MALEIC_ANHYDRIDE", "")),
    # Stefan's instruction of 13 October 2026, section 15: DEG, MEG, phthalic anhydride, maleic
    # anhydride, terephthalic acid and DMT feed the polyester polyols. The aromatic acids sit under
    # the aromatic family, the aliphatic ones under the aliphatic family, the glycols under both.
    "AROMATIC_POLYESTER_POLYOL": (("PHTHALIC_ANHYDRIDE", ""), ("TEREPHTHALIC_ACID", ""), ("DMT", ""),
                                  ("DIETHYLENE_GLYCOL", ""), ("ETHYLENE_GLYCOL", "")),
}


def feedstocks_of(chemical: str) -> tuple[tuple[str, str], ...]:
    """The feedstocks of this raw material, each with a note where the step is not direct."""
    return FEEDSTOCK_OF.get(str(chemical or "").strip().upper(), ())


def is_feedstock(chemical: str) -> bool:
    return str(chemical or "").strip().upper() in FEEDSTOCK_CHEMICALS


def chemical_kind(chemical: str) -> str:
    """Raw material or feedstock, for a column on Platform Admin."""
    return "Feedstock" if is_feedstock(chemical) else "Raw material"


# Every geography the store holds a price for, named as the assessment names it.
#
# This was China, Europe and North America, and everything else - Northeast Asia, Southeast Asia,
# India, Taiwan, the Middle East - was treated as evidence behind those three. ICIS assesses them as
# markets in their own right and a buyer sourcing from them wants to see them. Stefan, 26 September
# 2026: "same for geographies".
#
# East, North and South China are regions inside China rather than entries of their own: the page
# offers China and draws the three regional assessments within it.
ORIGINS = (
    "China", "Europe", "North America", "South America",
    "Northeast Asia", "Southeast Asia", "South Asia", "India",
    "South Korea", "Taiwan", "Japan",
    "Middle East", "GCC", "East Mediterranean", "Iran",
    # UN Comtrade reporters, 13 October 2026.
    "United States", "Saudi Arabia", "Türkiye",
)
# Every delivery term the assessments are published on. FD and DEL were missing, which is why 424
# weekly ICIS readings each of TDI, pMDI and pure MDI in Europe reached no screen at all.
# What the selectors offer, in the order a reader expects: the seller's end first, then the buyer's.
#
# One list of delivery terms lives in `market_basis.DELIVERY_TERMS` and this is a NARROWING of it
# (masterplan F): the terms the store actually holds a market on, ordered. It was a third
# hand-written list, and it disagreed with the other two about FD - which is the ICIS Western Europe
# history, 1,385 readings, and the reason this list has FD at all.
#
# Ordered here rather than sorted, because EXW..DDP is the journey and alphabetical is not.
INCOTERMS = ("EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP", "FD")
assert set(INCOTERMS) <= market_basis.DELIVERY_TERMS, sorted(
    set(INCOTERMS) - market_basis.DELIVERY_TERMS)
# A published market is a market price. A producer's own list price, a seller's quote and a
# transaction record are all real evidence, and each was being offered in the Incoterm selector as a
# market of its own - CHINA_PRODUCER_LIST, CHINA_DOMESTIC_SELLER_QUOTE, DELIVERED - because whoever
# wrote the connector put the kind of price into the basis field to keep it apart from the market
# series. Keeping it apart was right; offering it as a delivery term was not.
# The kind of price a market's headline is taken from, and what a reading with nothing stated is
# read as. One list, in `price_kinds`, which also holds the four values and what each of the old
# seven became (masterplan A, item 6).
PUBLISHED_OBSERVATION_TYPE = price_kinds.HEADLINE

# One basis that is not an Incoterm and is still a published market.
#
# businessanalytiq publishes monthly regional TDI assessments and states no delivery term, so the
# connector records REGIONAL_MARKET rather than inventing a CFR or an FOB the publisher never wrote.
# Excluding it on that ground took away the only live European TDI price we hold - a publisher's
# editorial choice deciding what a customer may see. Stefan, 21 September 2026: use it.
#
# It stays honest by being named for what it is in the selector rather than dressed as a delivery
# term. Everything else in that list is an Incoterm.
ASSESSMENT_BASES = ("REGIONAL_MARKET",)
# A published market is a published price, whatever the publisher calls the kind of price. A contract
# assessment, a transaction record and a formula-based settlement are all prices somebody paid or
# was quoted; excluding them kept 1,694 contract readings and 1,698 transaction readings off every
# screen. What stays out is a producer's own posting and a seller's quote, which are asks.
PUBLISHED_OBSERVATION_TYPES = price_kinds.PUBLISHED
PUBLISHED_BASES = INCOTERMS + ASSESSMENT_BASES
# The "terms" a producer's list or a seller's quote is filed under. They name the kind of price, not
# where it is delivered, and the price itself is a posting or a quote: evidence behind the EXW
# market. Every other basis is judged, stated terms or not (Stefan, 13 October 2026).
POSTING_BASES = frozenset({"CHINA_PRODUCER_LIST", "CHINA_PRODUCER_EXW_CASH",
                           "CHINA_PRODUCER_EXW_ACCEPTANCE", "CHINA_PRODUCER_DELIVERED_CASH",
                           "CHINA_DOMESTIC_SELLER_QUOTE"})

# Regions the page draws inside a country rather than offering as markets of their own.
_REGIONS_INSIDE_A_COUNTRY = ("East China", "North China", "South China")

# The markets with a governed series behind them. A lane opens when the readings are consolidated,
# not when a connector exists: a page that offers a market it has no price for is worse than one
# that does not offer it.
ACTIVE_LANES = {
    ("TDI", "China"),
    ("PMDI", "China"),
    ("POLYETHER_POLYOL", "China"),
}

_POLYOL_PACKAGING = ("Flexibag", "ISO Tank", "Bulk Road Tank", "Drum", "IBC")
_PACKAGING = {
    "TDI": ("ISO Tank", "Drum", "IBC"),
    "PMDI": ("ISO Tank", "Drum", "IBC"),
    # Added 26 September 2026 with the roster. Pure MDI moves like pMDI; rigid polyol like any
    # polyol; PO in bulk and tanks, never drummed at this scale.
    "PURE_MDI": ("ISO Tank", "Drum", "IBC"),
    # HDI and IPDI sat here from 28 October 2026 until 6 November, when they stopped being
    # chemicals this application tracks - `identity_vocabulary` carries why. A packaging entry for
    # a family the gate refuses is a second list saying something the first one denies.
    "RIGID_POLYOL": _POLYOL_PACKAGING,
    "PO": ("ISO Tank", "Bulk Road Tank"),
    "POLYETHER_POLYOL": _POLYOL_PACKAGING,
    "HR_POLYOL": _POLYOL_PACKAGING,
    "GRAFT_POLYOL": _POLYOL_PACKAGING,
    "PHD_POLYOL": _POLYOL_PACKAGING,
    "PIPA_POLYOL": _POLYOL_PACKAGING,
    "VISCO_POLYOL": _POLYOL_PACKAGING,
    "ALIPHATIC_POLYESTER_POLYOL": _POLYOL_PACKAGING,
    "AROMATIC_POLYESTER_POLYOL": _POLYOL_PACKAGING,
    "FATTY_ACID_ESTER_POLYOL": _POLYOL_PACKAGING,
}


def packaging_options(chemical: str) -> tuple[str, ...]:
    return _PACKAGING.get(chemical, tuple())


def market_title(chemical: str, origin: str) -> str:
    # The store keeps PMDI; the trade writes pMDI. See raw_material_forecasting/chemicals.py.
    return f"{display_chemical(chemical)} — {origin} Market"


def is_published_market(identity: dict) -> bool:
    """Whether a stored series is evaluated as a market a customer may be offered.

    Three conditions: a chemical the application knows (a PU chemical or a feedstock), any stated
    geography, and a price rather than a producer's posting or a seller's quote. A posting is an
    ask; on the line it would be averaged into the market, so announcements are shown in Market
    Intelligence instead.

    Stefan, 13 October 2026: feedstocks and series with no stated terms are no longer excluded by
    rule. Those rulings were made while there was too little data behind them; the eligibility
    table on Data & Sources now judges every series on what it holds, every time it is read.
    """
    # Stefan's own Yes in the Data & Sources table wins over every condition below.
    if forced_onto_pages(identity):
        return True
    geography = str(identity.get("market_geography") or "").strip()
    # Any geography the store prices is offered (Stefan, 13 October 2026: show as much as possible).
    # A fixed list hid every country nobody had typed into it yet. ORIGINS still orders the selector.
    basis = str(identity.get("commercial_basis") or "").strip().upper()
    return (is_offered_chemical(identity.get("product_family"))
            and bool(geography)
            and basis not in POSTING_BASES
            and price_kinds.is_published(
                identity.get("observation_type") or PUBLISHED_OBSERVATION_TYPE))


def is_offered_chemical(product_family: str) -> bool:
    """A PU chemical or a feedstock: every chemical the application holds a name for.

    The same question the store now asks at the door (masterplan D), from the same list. This is
    still worth asking here, because the store holds families named before the gate existed - four
    identities of `MDI`, which does not say whether it is polymeric or pure.
    """
    return identity_vocabulary.is_priced_family(product_family)


def why_not_published(identity: dict) -> str:
    """The first of `is_published_market`'s conditions this series fails, in a reader's words.

    Empty when it is published. Shown in the Data & Sources eligibility table (Stefan,
    13 October 2026), so a series missing from the customer pages carries its reason.
    """
    if not is_offered_chemical(identity.get("product_family")):
        return "Chemical not named in the application"
    if not str(identity.get("market_geography") or "").strip():
        return "No geography"
    if str(identity.get("commercial_basis") or "").strip().upper() in POSTING_BASES:
        return "Producer list or seller quote: evidence behind the market, not a price"
    kind = price_kinds.kind_of(identity.get("observation_type") or PUBLISHED_OBSERVATION_TYPE)
    if kind not in PUBLISHED_OBSERVATION_TYPES:
        # One reason, because the two it used to name are now one kind: an ask. A producer's
        # posting and a seller's quote are the same thing to a buyer - nobody has traded on either.
        return "An ask - a posting or an offer nobody traded on, not a price"
    return ""


def is_active_chemical(product_family: str) -> bool:
    """Whether a stored family is one of the markets this page offers.

    Codes are compared to codes. `ACTIVE_CHEMICALS` holds the store's codes, and so does the store,
    so both sides go through `canonical_code` and neither side is ever turned into a display name.
    """
    return canonical_code(product_family) in {canonical_code(c) for c in ACTIVE_CHEMICALS}


def lane_status(chemical: str, origin: str) -> str:
    lane = (canonical_code(chemical), str(origin or "").strip())
    if lane in {(canonical_code(c), o) for c, o in ACTIVE_LANES}:
        return "ACTIVE DEVELOPMENT LANE"
    return "DATA NOT YET QUALIFIED"
