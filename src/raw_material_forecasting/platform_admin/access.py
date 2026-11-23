from __future__ import annotations

from collections.abc import Iterable

from .models import EntitlementScope

PAGE_FEATURES = {
    "Current Market": "current_market",
    # Historic Pricing is the other half of Current Market - the markets we hold a record for and
    # are not collecting now (Stefan, 27 September 2026) - so it rides on the same entitlement. A
    # customer who may see a market's price may see where that price came from.
    "Historic Pricing": "current_market",
    "Forecast": "forecast",
    "Market Intelligence": "market_intelligence",
    "Chemical Relationships": "chemical_relationships",
    "Forecast Performance": "forecast_performance",
    "Delivered Price": "delivered_price",
    "Data & Sources": "data_sources",
}


def allowed_application_pages(scope: EntitlementScope, pages: Iterable[str]) -> list[str]:
    if not scope.application_access:
        return []
    if "*" in scope.features:
        return list(pages)
    return [page for page in pages if PAGE_FEATURES.get(page) in scope.features]


def filter_entitled_values(values: Iterable[str], allowed: frozenset[str]) -> list[str]:
    items = list(values)
    if "*" in allowed:
        return items
    allowed_norm = {str(value).strip().lower() for value in allowed}
    return [value for value in items if str(value).strip().lower() in allowed_norm]


def _display_market_dimension(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized.upper() in {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"}:
        return "Not specified"
    return normalized


def _dimension_allowed(value: str | None, allowed: frozenset[str]) -> bool:
    if "*" in allowed:
        return True
    normalized = _display_market_dimension(value).strip().lower()
    return normalized in {str(item).strip().lower() for item in allowed}


def identity_allowed(identity: dict, scope: EntitlementScope) -> bool:
    """Whether a customer may see this market at all.

    The chemical is asked through `chemical_allowed`, not through the plain string comparison the
    other three dimensions use, because it is the one dimension whose entitlement is written in
    different words from the store's. A geography is "China" on both sides; a chemical is sold as
    "Polyether Polyol" and stored as POLYETHER_POLYOL.

    It used to be the plain comparison. Measured on 22 November 2026 against an entitlement for
    TDI, PMDI and Polyether Polyol: TDI and PMDI matched their own codes, POLYETHER_POLYOL matched
    nothing, and the third chemical the customer had paid for appeared in no selector on any page.
    """
    return (
        chemical_allowed(identity.get("product_family"), scope.chemicals)
        and _dimension_allowed(identity.get("market_geography"), scope.geographies)
        and _dimension_allowed(identity.get("commercial_basis") or identity.get("incoterm_or_commercial_basis"), scope.incoterms)
        and _dimension_allowed(identity.get("packaging"), scope.packaging)
    )


# --------------------------------------------------------------------------- chemical entitlements

# An entitlement is sold in commercial names ("Polyol"), while the route graph and the price store
# speak in codes ("ALIPHATIC_POLYESTER_POLYOL"). This is the map between them, and it is deliberately
# generous in one direction only: a broad token grants the narrow chemicals under it, never the
# reverse. A customer entitled to "Polyester Polyol" does not thereby see polyether.
CHEMICAL_SCOPE_ALIASES: dict[str, frozenset[str]] = {
    "TDI": frozenset({"TDI"}),
    "MDI": frozenset({"PMDI", "MONOMERIC_MDI", "CRUDE_MDI"}),
    "PMDI": frozenset({"PMDI", "CRUDE_MDI"}),
    "MONOMERIC MDI": frozenset({"MONOMERIC_MDI"}),
    # HR and graft are polyethers, so "Polyether Polyol" grants them and the group token grants all
    # five. A customer sold plain polyether before they existed keeps seeing what they bought and
    # gains the two that belong to the same chain, which is the direction this map is generous in.
    "POLYOL": frozenset({
        "POLYETHER_POLYOL", "HR_POLYOL", "GRAFT_POLYOL", "PHD_POLYOL", "PIPA_POLYOL",
        "VISCO_POLYOL", "ALIPHATIC_POLYESTER_POLYOL", "AROMATIC_POLYESTER_POLYOL",
        "FATTY_ACID_ESTER_POLYOL",
    }),
    # PHD and PIPA are polyether-based like the grafts, so the chain token grants them too.
    "POLYETHER POLYOL": frozenset({"POLYETHER_POLYOL", "HR_POLYOL", "GRAFT_POLYOL",
                                   "PHD_POLYOL", "PIPA_POLYOL", "VISCO_POLYOL"}),
    "VISCO POLYOL": frozenset({"VISCO_POLYOL"}),
    # Sold together as "polymer polyol" where a customer wants the filled grades and nothing else.
    "POLYMER POLYOL GROUP": frozenset({"GRAFT_POLYOL", "PHD_POLYOL", "PIPA_POLYOL"}),
    "PHD POLYOL": frozenset({"PHD_POLYOL"}),
    "PIPA POLYOL": frozenset({"PIPA_POLYOL"}),
    "HR POLYOL": frozenset({"HR_POLYOL"}),
    # POP is the same material under another name - Stefan, 17 September 2026 - so an entitlement
    # sold as either word grants the one series.
    "GRAFT POLYOL": frozenset({"GRAFT_POLYOL"}),
    "POP": frozenset({"GRAFT_POLYOL"}),
    "POLYMER POLYOL": frozenset({"GRAFT_POLYOL"}),
    "POLYESTER POLYOL": frozenset({"ALIPHATIC_POLYESTER_POLYOL", "AROMATIC_POLYESTER_POLYOL",
                                   "FATTY_ACID_ESTER_POLYOL"}),
    "FATTY ACID ESTER POLYOL": frozenset({"FATTY_ACID_ESTER_POLYOL"}),
    "ALIPHATIC POLYESTER POLYOL": frozenset({"ALIPHATIC_POLYESTER_POLYOL"}),
    "AROMATIC POLYESTER POLYOL": frozenset({"AROMATIC_POLYESTER_POLYOL"}),
}


def _token(value: str) -> str:
    return str(value or "").strip().upper().replace("_", " ")


def chemical_allowed(code: str, allowed: frozenset[str]) -> bool:
    """Whether an entitlement covers one chemical, by code or by the commercial name it is sold under.

    `allowed` is what the customer bought. `code` is a route-graph chemical. An unmapped token still
    matches its own code, so a chemical added to the graph before it is added to the map is simply
    not granted by any broad token - which is the safe direction to fail.
    """
    if "*" in allowed:
        return True
    target = _token(code)
    for entry in allowed:
        token = _token(entry)
        if token == target:
            return True
        if target in {_token(item) for item in CHEMICAL_SCOPE_ALIASES.get(token, frozenset())}:
            return True
    return False


def entitled_chemicals(codes: Iterable[str], allowed: frozenset[str]) -> list[str]:
    """The chemicals from `codes` this entitlement covers, order preserved."""
    return [code for code in codes if chemical_allowed(code, allowed)]
