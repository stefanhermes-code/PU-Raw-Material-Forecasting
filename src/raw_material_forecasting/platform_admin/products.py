"""What HTC sells: three products, three material groups, three origin regions, one price list.

CR-2026-11-26-01, first piece of work. Stefan, 26 November 2026, on the deck shown to Sheela Foam:
*"In this presentation there are 3 pricing models, check the application if these models coincide
with the subscription methods. If not make a CR to implement these."* They did not. A subscription
was a scope ticked by hand - chemicals one by one, geographies one by one - and nothing in the
application knew what a customer had bought, for which group, for how many regions, or for how much.

Everything here is the deck's pages 9, 10 and 11 written down once, and Stefan's answers of the
same day to the questions those pages left open:

  regions      *"The 3 regions are the traditional regions: APAC, Europe, North-America for now,
               later we may add MEA once the strait of hormuz is back."*
  feedstocks   *"these chemicals are the feedstock that belong to the raw materials from that
               group."*  No depth and no hand-kept list: the route graph says which they are.
  stacking     products are sold separately; a customer can hold more than one.
  Rigid Foam   sold as it is, although two of its three materials hold no current price.
  the trial    *"The trial is outside the current subscriptions, do not put it under anything."*
               Nothing here knows the trial exists.
  the prices   held here for reference. No invoicing and no payment.

Pure: no store, no screen. `scope_for` turns a choice into the `EntitlementScope` the rest of the
application already enforces, so every existing check stays exactly where it was.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .models import EntitlementScope

# ------------------------------------------------------------------------------------- the products
FORECAST_ALERTS = "FORECAST_ALERTS"
PRICE_MONITOR = "PRICE_MONITOR"
DIRECT_ACCESS = "DIRECT_ACCESS"

ANNUAL = "ANNUAL"            # Alerts and Direct Access: one price a year, nothing to choose
QUARTERLY = "QUARTERLY"      # the Price Monitor's two editions
MONTHLY = "MONTHLY"


@dataclass(frozen=True)
class Offer:
    """One thing a customer can be sold: a product, and for the Monitor which edition of it."""
    product: str
    edition: str
    name: str
    delivers: str            # the deck's own line for it

    @property
    def key(self) -> str:
        return f"{self.product}:{self.edition}"


OFFERS: tuple[Offer, ...] = (
    Offer(DIRECT_ACCESS, ANNUAL, "Direct Access",
          "On-demand access to the price forecasting application"),
    Offer(PRICE_MONITOR, QUARTERLY, "Price Monitor, quarterly",
          "A written report every quarter"),
    Offer(PRICE_MONITOR, MONTHLY, "Price Monitor, monthly",
          "A written report every month"),
    Offer(FORECAST_ALERTS, ANNUAL, "Forecast Alerts",
          "An email when a forecast reaches a threshold or changes direction"),
)
_OFFER = {offer.key: offer for offer in OFFERS}

# What the application can actually do for a customer who holds each product, today. Direct Access
# works. The other two are recorded and priced, and nothing is sent: the application cannot send an
# email yet (WP2), makes no report for a subscription (WP4) and raises no alert (WP5). The screen
# says so beside the choice, so that a product is not sold on the strength of having been saved.
DELIVERED_TODAY = {DIRECT_ACCESS: True, PRICE_MONITOR: False, FORECAST_ALERTS: False}
NOT_DELIVERED_YET = {
    PRICE_MONITOR: "Recorded and priced. The report is not yet made or sent for a subscription.",
    FORECAST_ALERTS: "Recorded and priced. No alert is raised or sent yet.",
}


def offer(product: str, edition: str) -> Offer:
    found = _OFFER.get(f"{product}:{edition}")
    if found is None:
        raise ValueError(f"not a product HTC sells: {product} / {edition}")
    return found


# -------------------------------------------------------------------------------- the material groups
FLEXIBLE_FOAM = "FLEXIBLE_FOAM"
RIGID_FOAM = "RIGID_FOAM"
FULL_CHAIN = "FULL_CHAIN"


@dataclass(frozen=True)
class MaterialGroup:
    code: str
    name: str
    # The store's own codes, because that is what a market is filed under. Empty for Full Chain,
    # which is "the complete tracked catalog" and is therefore everything rather than a list.
    materials: tuple[str, ...]
    said: str                # the deck's sentence, pages 9 to 11


GROUPS: tuple[MaterialGroup, ...] = (
    MaterialGroup(FLEXIBLE_FOAM, "Flexible Foam",
                  ("TDI", "PMDI", "POLYETHER_POLYOL", "ALIPHATIC_POLYESTER_POLYOL"),
                  "TDI, polymeric MDI, polyether polyol and aliphatic polyester polyol. "
                  "Related feedstocks included."),
    MaterialGroup(RIGID_FOAM, "Rigid Foam",
                  ("PMDI", "RIGID_POLYOL", "AROMATIC_POLYESTER_POLYOL"),
                  "Polymeric MDI, rigid-grade polyether and aromatic polyester polyols. "
                  "Related feedstocks included."),
    MaterialGroup(FULL_CHAIN, "Full Chain", (),
                  "The complete tracked catalog. Includes both foam groups and other tracked "
                  "materials and feedstocks."),
)
_GROUP = {group.code: group for group in GROUPS}

# A material the store files under a code the route graph has no node for. Rigid-grade polyether
# is made the way polyether polyol is made - the graph holds one polyether - so its feedstocks are
# read from that chain. Without this line the Rigid group's polyether would bring no feedstock.
ROUTE_OF = {"RIGID_POLYOL": "POLYETHER_POLYOL"}


def group(code: str) -> MaterialGroup:
    found = _GROUP.get(code)
    if found is None:
        raise ValueError(f"not a material group HTC sells: {code}")
    return found


# ---------------------------------------------------------------------------------- the origin regions
APAC = "APAC"
EUROPE = "EUROPE"
NORTH_AMERICA = "NORTH_AMERICA"


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    # Every name the store files a market under that lies in this region. A market is opened by
    # its own name, so a region is the list of them and nothing cleverer.
    geographies: tuple[str, ...]


# Stefan named the three regions. Which of the store's market names lies in which is the writer's
# reading, made against the 26 names the live store held on 26 November 2026, and it is drawn on the
# Products page ("Where each market sits") so that it is read by the person who can correct it.
# The one that could be argued is Türkiye with the East Mediterranean: placed in Europe, where the
# price assessors quote it, and not held back with the Gulf.
REGIONS: tuple[Region, ...] = (
    Region(APAC, "APAC", (
        "China", "East China", "North China", "South China", "Shandong",
        "Northeast Asia", "Japan", "South Korea", "Taiwan",
        "Southeast Asia", "Thailand", "Vietnam", "Indonesia", "Malaysia", "Singapore",
        "South Asia", "India", "Asia", "Asia Pacific", "Australia",
    )),
    Region(EUROPE, "Europe", (
        "Europe", "Western Europe", "Northwest Europe", "Germany", "Netherlands", "Italy",
        "Türkiye", "Turkey", "East Mediterranean",
    )),
    Region(NORTH_AMERICA, "North America", (
        "North America", "United States", "USA", "US Gulf", "Canada", "Mexico",
    )),
)
_REGION = {region.code: region for region in REGIONS}

# Markets held and deliberately not sold yet. *"later we may add MEA once the strait of hormuz is
# back"*: these are the names MEA will be made of, kept here so that adding the fourth region is
# moving this tuple into REGIONS and giving it a price, and so that the page can say of a Gulf
# market "held back" rather than "nobody knows where this is".
HELD_BACK_FOR_MEA: tuple[str, ...] = ("Middle East", "Saudi Arabia", "GCC", "Iran", "Africa")


def region(code: str) -> Region:
    found = _REGION.get(code)
    if found is None:
        raise ValueError(f"not an origin region HTC sells: {code}")
    return found


def _fold(name: str) -> str:
    return str(name or "").strip().casefold()


def region_of(geography: str) -> str | None:
    """The region a market name lies in, or None when it lies in none that is sold."""
    wanted = _fold(geography)
    for one in REGIONS:
        if wanted in {_fold(name) for name in one.geographies}:
            return one.code
    return None


def where(geography: str) -> str:
    """One word for the page: the region's name, "Held back for MEA", or "No region"."""
    code = region_of(geography)
    if code is not None:
        return region(code).name
    if _fold(geography) in {_fold(name) for name in HELD_BACK_FOR_MEA}:
        return "Held back for MEA"
    return "No region"


def geographies_of(regions: Iterable[str]) -> frozenset[str]:
    return frozenset(name for code in regions for name in region(code).geographies)


# ---------------------------------------------------------------------------------------- the prices
# US dollars per company per year, for one, two and three origin regions. Pages 9, 10 and 11 of the
# deck, read on 26 November 2026. Thirty-six prices; a change to the deck is a change here.
PRICES: dict[str, dict[str, tuple[int, int, int]]] = {
    f"{FORECAST_ALERTS}:{ANNUAL}": {
        FLEXIBLE_FOAM: (2_400, 2_650, 2_900), RIGID_FOAM: (2_400, 2_650, 2_900),
        FULL_CHAIN: (3_600, 4_000, 4_400)},
    f"{PRICE_MONITOR}:{QUARTERLY}": {
        FLEXIBLE_FOAM: (2_000, 2_200, 2_400), RIGID_FOAM: (2_000, 2_200, 2_400),
        FULL_CHAIN: (3_400, 3_750, 4_100)},
    f"{PRICE_MONITOR}:{MONTHLY}": {
        FLEXIBLE_FOAM: (6_000, 6_600, 7_250), RIGID_FOAM: (6_000, 6_600, 7_250),
        FULL_CHAIN: (7_500, 8_250, 9_000)},
    f"{DIRECT_ACCESS}:{ANNUAL}": {
        FLEXIBLE_FOAM: (9_000, 10_000, 11_000), RIGID_FOAM: (9_000, 10_000, 11_000),
        FULL_CHAIN: (12_000, 13_200, 14_500)},
}
CURRENCY = "USD"


def fee(product: str, edition: str, material_group: str, regions: int) -> int:
    """The annual price of one choice. Refuses a choice the deck does not price."""
    chosen = offer(product, edition)
    group(material_group)
    if not 1 <= int(regions) <= len(REGIONS):
        raise ValueError(f"the price list covers 1 to {len(REGIONS)} origin regions, not {regions}")
    return PRICES[chosen.key][material_group][int(regions) - 1]


def price_list() -> list[dict[str, object]]:
    """The whole list, one row for each product and group, as the page shows it."""
    rows = []
    for one in OFFERS:
        for held in GROUPS:
            one_region, two, three = PRICES[one.key][held.code]
            rows.append({"Product": one.name, "Material group": held.name,
                         "1 region": one_region, "2 regions": two, "3 regions": three})
    return rows


# ------------------------------------------------------------------------------------ the feedstocks
def _seed() -> Path:
    from raw_material_forecasting.chemical_routes import DEFAULT_SEED

    beside_the_code = Path(__file__).resolve().parents[3] / DEFAULT_SEED
    return beside_the_code if beside_the_code.exists() else Path(DEFAULT_SEED)


@lru_cache(maxsize=4)
def _graph_at(path: str, changed: float):
    from raw_material_forecasting.chemical_routes import load_graph

    return load_graph(path)


def route_graph():
    """The route graph the Chemical Relationships page draws, read once for each version of it."""
    seed = _seed()
    return _graph_at(str(seed), seed.stat().st_mtime)


def feedstocks(material_group: str, graph=None) -> tuple[str, ...]:
    """The priced chemicals that belong to a group's raw materials. Stefan's rule, as stated.

    Every quoted chemical upstream of any of the group's materials, to the top of the chain: the
    rule has no depth, so crude oil and naphtha are in every group. A captive intermediate -
    phosgene, DNT - is walked through and not listed, because nobody quotes it and there is nothing
    to open. Full Chain has no list: it is everything.
    """
    held = group(material_group)
    if not held.materials:
        return ()
    graph = graph or route_graph()
    found: set[str] = set()
    for material in held.materials:
        for candidate in graph.upstream_candidates(ROUTE_OF.get(material, material),
                                                   max_priced_steps=99):
            found.add(candidate.chemical)
    return tuple(sorted(found - set(held.materials)))


# ------------------------------------------------------------------------------- a choice as a scope
# What Direct Access opens, in the deck's words: "Check prices for your selected materials and
# origin regions. Review forecasts, look back at price history and follow market developments."
# Price history rides on current_market (`access.PAGE_FEATURES`).
DIRECT_ACCESS_FEATURES = frozenset({"current_market", "forecast", "market_intelligence"})
# "90%+ accuracy through the three-month outlook" (the deck, page 8), and what the trial was given.
HORIZON_MONTHS = 3
EVERYTHING = frozenset({"*"})


def scope_for(product: str, edition: str, material_group: str, regions: Iterable[str],
              graph=None) -> EntitlementScope:
    """What one choice opens, as the scope the application already enforces.

    Chemicals are the group's materials and their feedstocks, by the store's codes; geographies are
    every market name in the regions bought. Delivery terms and packaging are not sold separately,
    so both are open. Only Direct Access opens the application: the Monitor and Alerts are sent to
    a customer, and their scope says what may be in what is sent.
    """
    chosen = offer(product, edition)
    held = group(material_group)
    regions = frozenset(regions)
    for code in regions:
        region(code)
    chemicals = (EVERYTHING if not held.materials
                 else frozenset(held.materials) | frozenset(feedstocks(held.code, graph)))
    common = dict(chemicals=chemicals, geographies=geographies_of(regions), incoterms=EVERYTHING,
                  packaging=EVERYTHING, forecast_horizon_months=HORIZON_MONTHS)
    if chosen.product == DIRECT_ACCESS:
        return EntitlementScope(application_access=True, features=DIRECT_ACCESS_FEATURES, **common)
    if chosen.product == PRICE_MONITOR:
        return EntitlementScope(
            features=frozenset({"reports"}), report_formats=frozenset({"PDF"}),
            delivery_frequencies=frozenset({"Monthly" if edition == MONTHLY else "Quarterly"}),
            delivery_channels=frozenset({"Email"}), **common)
    return EntitlementScope(features=frozenset({"forecast"}),
                            delivery_channels=frozenset({"Email"}), **common)
