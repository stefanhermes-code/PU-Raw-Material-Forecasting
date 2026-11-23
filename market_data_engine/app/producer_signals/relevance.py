"""How a chemical relates to the chemicals HTC actually sells.

The event lane collects the whole polyurethane board on purpose: a chlorine outage or a
propylene-oxide start-up moves isocyanate economics long before anything shows up in a price. But a
row that says only "DMF" leaves the reader to work out the connection, and most will not.

This is a GRAPH, not a list of sentences about TDI. The first version hard-wired TDI into every
phrase, which was only ever true while the portfolio held one chemical - the moment PMDI or polyol is
sold too, "no direct TDI link" is the wrong thing to print next to an aniline signal that is highly
relevant to MDI. So relationships are stored as edges between chemicals and the sentence is composed
against whichever chemicals are in the portfolio at the time (`PORTFOLIO_CHEMICALS`, default TDI).

It remains an INTERIM. The governed Chemical Relationships model does not exist yet; when it does,
these edges are its specification and this module is what it replaces.

Edges describe the chain, not a forecast: "chlorine is a feedstock for TDI" is a fact about how TDI is
made and carries no claim about direction or size.
"""
from __future__ import annotations

import os

DEFAULT_PORTFOLIO = ("TDI",)

FEEDSTOCK = "FEEDSTOCK"        # X is a feedstock for Y
INTERMEDIATE = "INTERMEDIATE"  # X is an intermediate on the way to Y
CO_INPUT = "CO_INPUT"          # X is bought together with Y to make a system
SUBSTITUTE = "SUBSTITUTE"      # X competes with Y

# Display names, so a sentence reads the way a buyer speaks.
NAMES: dict[str, str] = {
    "TDI": "TDI", "PMDI": "polymeric MDI", "PURE_MDI": "pure MDI", "MDI_50": "MDI-50",
    "POLYOL": "polyether polyol", "PO": "propylene oxide", "EO": "ethylene oxide",
    "TOLUENE": "toluene", "LIQUID_CHLORINE": "chlorine", "CHLORINE": "chlorine",
    "NITRIC_ACID": "nitric acid", "ANILINE": "aniline", "DNT": "DNT", "TDA": "TDA",
    "DMF": "DMF", "NPG": "neopentyl glycol", "BDO": "BDO", "ADIPIC_ACID": "adipic acid",
    "TMP": "TMP", "TPU": "TPU",
    # Added 29 September 2026 with the first polyester-polyol announcement we hold. Without a name
    # the sentence on the screen reads "raised its chemical price".
    "ALIPHATIC_POLYESTER_POLYOL": "aliphatic polyester polyol",
    "AROMATIC_POLYESTER_POLYOL": "aromatic polyester polyol",
}

# chemical -> edges out of it. (relation, target, qualifier)
EDGES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "TOLUENE": (("FEEDSTOCK", "TDI", ""),),
    "NITRIC_ACID": (("FEEDSTOCK", "TDI", ""),),
    "LIQUID_CHLORINE": (("FEEDSTOCK", "TDI", "via phosgene"),
                        ("FEEDSTOCK", "PMDI", "via phosgene"),
                        ("FEEDSTOCK", "PURE_MDI", "via phosgene")),
    "CHLORINE": (("FEEDSTOCK", "TDI", "via phosgene"),
                 ("FEEDSTOCK", "PMDI", "via phosgene")),
    "DNT": (("INTERMEDIATE", "TDI", ""),),
    "TDA": (("INTERMEDIATE", "TDI", ""),),
    "ANILINE": (("FEEDSTOCK", "PMDI", ""), ("FEEDSTOCK", "PURE_MDI", "")),
    "PO": (("FEEDSTOCK", "POLYOL", ""),),
    # Adipic acid is what an aliphatic polyester polyol is made from, and a rigid-foam polyester
    # polyol is bought with polymeric MDI the way a flexible-foam polyether is bought with TDI.
    "ADIPIC_ACID": (("FEEDSTOCK", "ALIPHATIC_POLYESTER_POLYOL", ""),),
    "AROMATIC_POLYESTER_POLYOL": (("CO_INPUT", "PMDI", "rigid foam"),),
    "POLYOL": (("CO_INPUT", "TDI", "flexible foam"),
               ("CO_INPUT", "PMDI", "rigid foam")),
    "PMDI": (("SUBSTITUTE", "TDI", "shares the phosgene chain"),
             ("CO_INPUT", "POLYOL", "rigid foam")),
    "PURE_MDI": (("SUBSTITUTE", "TDI", "shares the phosgene chain"),),
    "MDI_50": (("SUBSTITUTE", "TDI", "shares the phosgene chain"),),
    "TDI": (("SUBSTITUTE", "PMDI", "shares the phosgene chain"),
            ("CO_INPUT", "POLYOL", "flexible foam")),
}

# How a direct link reads on its own, and how it reads as the tail of a two-hop sentence.
_RELATION_PHRASE = {
    "FEEDSTOCK": "Feedstock for {target}",
    "INTERMEDIATE": "Intermediate on the way to {target}",
    "CO_INPUT": "Bought with {target} in {qualifier}",
    "SUBSTITUTE": "Competes with {target}",
}
_ONWARD_CLAUSE = {
    "FEEDSTOCK": "itself a feedstock for {target}",
    "INTERMEDIATE": "itself an intermediate to {target}",
    "CO_INPUT": "itself bought with {target} in {qualifier}",
    "SUBSTITUTE": "which competes with {target}",
}

# Ranked strongest first, so the sentence leads with the closest link.
_RELATION_RANK = ("FEEDSTOCK", "INTERMEDIATE", "CO_INPUT", "SUBSTITUTE")

# Used when one chemical is bought with two portfolio chemicals into different systems - polyol goes
# into flexible foam with TDI and rigid foam with PMDI, so naming one of them would be wrong.
_MIXED_SYSTEM = "polyurethane systems"


def portfolio_chemicals() -> tuple[str, ...]:
    """The chemicals HTC currently sells. TDI today; this is not a permanent assumption."""
    configured = os.environ.get("PORTFOLIO_CHEMICALS", "")
    parts = tuple(part.strip().upper() for part in configured.split(",") if part.strip())
    return parts or DEFAULT_PORTFOLIO


def display_name(chemical: str) -> str:
    key = str(chemical or "").strip().upper()
    return NAMES.get(key, key.replace("_", " ").lower())


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def _edge_into_portfolio(chemical: str, portfolio: tuple[str, ...]):
    """Strongest direct edge from `chemical` into the portfolio, as (relation, names, qualifier)."""
    for relation in _RELATION_RANK:
        targets = [(target, qualifier) for rel, target, qualifier in EDGES.get(chemical, ())
                   if rel == relation and target in portfolio]
        if not targets:
            continue
        names = _join([display_name(target) for target, _ in targets])
        qualifiers = {qualifier for _, qualifier in targets if qualifier}
        if len(qualifiers) == 1:
            qualifier = next(iter(qualifiers))
        elif qualifiers:
            qualifier = _MIXED_SYSTEM if relation == "CO_INPUT" else ""
        else:
            qualifier = ""
        return relation, names, qualifier
    return None


def _render(relation: str, names: str, qualifier: str, *, onward: bool = False) -> str:
    template = (_ONWARD_CLAUSE if onward else _RELATION_PHRASE)[relation]
    phrase = template.format(target=names, qualifier=qualifier or _MIXED_SYSTEM)
    if relation not in {"CO_INPUT"} and qualifier:
        phrase = f"{phrase}, {qualifier}"
    return phrase


def _direct(chemical: str, portfolio: tuple[str, ...]) -> tuple[str, str] | None:
    edge = _edge_into_portfolio(chemical, portfolio)
    if edge is None:
        return None
    relation, names, qualifier = edge
    return relation, _render(relation, names, qualifier)


def _one_hop(chemical: str, portfolio: tuple[str, ...]) -> tuple[str, str] | None:
    """X feeds Y, and Y is itself tied to the portfolio.

    "Feedstock for polyether polyol, itself bought with TDI in flexible foam" - two hops is as far as
    this goes. Beyond that every chemical is related to every other one and the sentence stops meaning
    anything.
    """
    for relation, target, _qualifier in EDGES.get(chemical, ()):
        if relation not in {"FEEDSTOCK", "INTERMEDIATE"} or target in portfolio:
            continue
        edge = _edge_into_portfolio(target, portfolio)
        if edge is None:
            continue
        lead = _RELATION_PHRASE[relation].format(target=display_name(target), qualifier="")
        return relation, f"{lead}, {_render(*edge, onward=True)}"
    return None


def relationship(chemical: str | None, portfolio: tuple[str, ...] | None = None) -> tuple[str, str]:
    """(relation, phrase). ("", "") when nothing true can be said - silence beats a guessed link."""
    key = str(chemical or "").strip().upper()
    if not key:
        return "", ""
    active = tuple(portfolio) if portfolio else portfolio_chemicals()
    if key in active:
        return "PORTFOLIO", f"The {display_name(key)} market itself"
    found = _direct(key, active) or _one_hop(key, active)
    if found is not None:
        return found
    if key in EDGES or key in NAMES:
        return "NONE", "Polyurethane chain, no direct link to the portfolio"
    return "", ""


def relevance_phrase(chemical: str | None, portfolio: tuple[str, ...] | None = None) -> str:
    return relationship(chemical, portfolio)[1]
