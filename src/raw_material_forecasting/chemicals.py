"""How a chemical's name is written on a screen.

The polyurethane trade writes polymeric MDI as pMDI - lower-case p, capital MDI - in datasheets,
quotations and market reports. The lower-case p is the point: it marks the p as a prefix on MDI
rather than a fourth letter of an acronym, the same way it separates pMDI from MDI itself.

The STORE keeps PMDI and this does not touch it. A stored product family is part of an identity's
comparability group - product, grade, geography, basis, packaging, currency, unit - so rewriting it
in the database would split every series in two, which is the fault migration 0021 exists to clean
up. The store keeps the key; the screen says pMDI.

Anything else is passed through as it was written.
"""
from __future__ import annotations

# Stored key -> what a reader sees. Keyed on the upper-case form, so "pmdi", "Pmdi" and "PMDI" all
# arrive at the same place.
DISPLAY_NAMES: dict[str, str] = {
    "PMDI": "pMDI",
    # The store's codes, written the way the trade writes them. POP and graft polyol are one
    # material under two names - Stefan, 17 September 2026 - so there is one code and the screen
    # carries both words.
    # Retired as a family by migration 0035; kept so an older export still reads.
    "POLYOL": "Polyether Polyol",
    "POLYETHER_POLYOL": "Polyether Polyol",
    "HR_POLYOL": "HR Polyol",
    # Added 26 September 2026 when the roster opened. Both carry readings - 2,106 of pure MDI and
    # 732 of rigid polyol - and both would have reached the selector as SHOUTING_WITH_UNDERSCORES.
    #
    # Pure MDI is the distilled monomeric fraction, and the route graph calls the same material
    # MONOMERIC_MDI while the store calls it PURE_MDI. One material, so one name on screen.
    "PURE_MDI": "Pure MDI",
    "RIGID_POLYOL": "Rigid Polyol",
    "GRAFT_POLYOL": "Graft Polyol (POP)",
    "PHD_POLYOL": "PHD Polyol",
    "PIPA_POLYOL": "PIPA Polyol",
    "VISCO_POLYOL": "Visco Polyol",
    "FATTY_ACID_ESTER_POLYOL": "Fatty Acid Ester Polyol",
    "ALIPHATIC_POLYESTER_POLYOL": "Aliphatic Polyester Polyol",
    "AROMATIC_POLYESTER_POLYOL": "Aromatic Polyester Polyol",
    # Feedstocks and intermediates. They have route-graph codes rather than trade names, and the
    # codes are not what a reader should be shown: PO is propylene oxide and ADIPIC_ACID is not a
    # way to write adipic acid. Added 19 September 2026 with the first feedstock series.
    "PO": "Propylene Oxide",
    "EO": "Ethylene Oxide",
    "ANILINE": "Aniline",
    "ADIPIC_ACID": "Adipic Acid",
    "DIETHYLENE_GLYCOL": "Diethylene Glycol",
    "ETHYLENE_GLYCOL": "Ethylene Glycol",
    "PHTHALIC_ANHYDRIDE": "Phthalic Anhydride",
    "MALEIC_ANHYDRIDE": "Maleic Anhydride",
    "TEREPHTHALIC_ACID": "Terephthalic Acid",
    "DMT": "Dimethyl Terephthalate",
    "BENZENE": "Benzene",
    "TOLUENE": "Toluene",
    "PROPYLENE": "Propylene",
    "NITROBENZENE": "Nitrobenzene",
    "FORMALDEHYDE": "Formaldehyde",
    "METHANOL": "Methanol",
    "CAUSTIC_SODA": "Caustic Soda",
    "CHLORINE": "Chlorine",
    "MDA": "MDA",
    "TDA": "TDA",
    "DNT": "DNT",
    "PHOSGENE": "Phosgene",
    "CRUDE_MDI": "Crude MDI",
    "MONOMERIC_MDI": "Pure MDI",
    "STYRENE_MONOMER": "Styrene",
    "BDO": "BDO",
    "NPG": "Neopentyl Glycol",
    "GLYCEROL": "Glycerol",
}


# Store codes that have been renamed, old -> current. Migration 0035 renamed POLYOL to
# POLYETHER_POLYOL. A bootstrap file, an export or a store that has not been migrated yet can still
# carry the old word, and it has to land on the same market rather than on none.
#
# `MDI` is the second of them, and it is a RULING rather than a rename. **Stefan, 28 October 2026:**
# *"for now I take the decision to keep them as pMDI. We can always come back to this later and make
# necessary changes."*
#
# The question was raised by D's gate, which refused `MDI` because the store prices PMDI (polymeric)
# and PURE_MDI (monomeric) separately and the bare word does not say which. Four identities and nine
# readings carried it, five of them HTC's own purchase orders - one `Lupranate MX118/1` from BASF and
# four `W8223` from Wanhua. Looked up rather than guessed: BASF's sheet calls MX 118/1 a product
# based on 4,4'-MDI at about 32.5% NCO and 25 mPa.s, which is monomeric and far too thin to be
# polymeric, and Wanhua's WANNATE 8000 series are modified MDI prepolymers. So neither is cleanly
# either family and the honest answer is a third one - which is why this is recorded as a decision
# to revisit rather than as a fact. It is folded here, in the one place every reader and writer
# already asks, exactly as POLYOL is; migration 0063 moved the stored rows.
LEGACY_CODES: dict[str, str] = {
    "POLYOL": "POLYETHER_POLYOL",
    "MDI": "PMDI",
    # The intelligence writes the name a Chinese plant notice uses; the route graph's node is
    # CHLORINE. Two events, and without this they are upstream of TDI through phosgene and reach
    # nothing. Measured 29 October 2026.
    "LIQUID_CHLORINE": "CHLORINE",
}


def canonical_code(value) -> str:
    """The store code a family is compared under: upper-cased, with renamed codes folded forward.

    Codes are compared to codes and names are shown to readers. Comparing a name to a code is what
    took the polyether polyol page dark on 18 September 2026: `display_chemical` turned the stored
    POLYETHER_POLYOL into "Polyether Polyol", which does not equal the code "POLYETHER_POLYOL", so
    every polyol identity was filtered out of a page that held 104 readings for it.
    """
    text = str(value or "").strip().upper()
    return LEGACY_CODES.get(text, text)


# Every display name, read backwards: "ethylene oxide" -> EO.
#
# Derived rather than written out, which is the point. The feature store files a material three ways
# at once - snake-case trade names (`ethylene_oxide`, `adipic_acid`), store codes (`TDI`, `PMDI`) and
# a retired code (`POLYOL`) - and a hand list mapping those onto families would be a second
# vocabulary to keep in step with this one. `DISPLAY_NAMES` already holds the pairing; this only
# reads it the other way.
_CODE_BY_NAME: dict[str, str] = {}
for _code, _name in DISPLAY_NAMES.items():
    _CODE_BY_NAME.setdefault(_name.strip().lower(), _code)


def family_of(material) -> str:
    """The store's family code for a material however it is written, or the value upper-cased.

    `TDI` -> TDI, `POLYOL` -> POLYETHER_POLYOL (migration 0035), `ethylene_oxide` -> EO,
    `dimethyl_terephthalate` -> DMT, `propylene_oxide` -> PO. A name nothing recognises comes back
    as `canonical_code` leaves it, so a new material is visible rather than silently folded.

    Written for the feature store, which carries its material in `data["material"]` and had NO
    chemical filter at all: every one of its 117,146 rows was handed to every forecast, so TDI's
    forecast read 117,146 features of which 18 were TDI's.
    """
    text = str(material or "").strip()
    if not text:
        return ""
    code = canonical_code(text)
    if code in DISPLAY_NAMES:
        return code
    return _CODE_BY_NAME.get(text.replace("_", " ").lower(), code)


def display_chemical(value) -> str:
    """The chemical as a reader should see it: "PMDI" -> "pMDI".

    A code with no entry is returned exactly as it came in, and that is load-bearing rather than
    lazy: `period_report.route_chain` reads an unchanged answer as "this application has no name
    of its own for it" and falls back to the route graph's curated name, which is how the chain
    says "Crude oil" instead of "CRUDE_OIL". Writing unknown codes out here was tried on
    12 October 2026 and took that fallback away: every box in the chain was renamed from the
    graph's wording to a mechanical one, "Crude oil" becoming "Crude OIL". A code that reaches a
    reader badly is named in DISPLAY_NAMES above, which is the one place a name belongs.
    """
    text = str(value or "").strip()
    return DISPLAY_NAMES.get(text.upper(), text)
