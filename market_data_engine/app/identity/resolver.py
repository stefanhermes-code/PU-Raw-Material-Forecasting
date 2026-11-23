from app.domain.schemas import MarketIdentity
from app.domain.series_key import comparability_group
from raw_material_forecasting import units
from raw_material_forecasting.units import canonical_unit
from raw_material_forecasting import chemical_vocabulary, price_kinds

UNKNOWN_PACKAGING_TOKENS = {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED", "N/A", "NA", "NONE", "NULL"}

# A quote whose Incoterm the publisher did not state. Stefan, 19 October 2026: keep the price under
# "Unspecified" rather than drop it for want of a term. One spelling, so that "Not stated",
# "not specified" and "-" on three documents make one series, not three.
UNSPECIFIED_BASIS = "UNSPECIFIED"
_UNSPECIFIED_BASIS_TOKENS = {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED", "NOT STATED",
                             "NOT_STATED", "N/A", "NA", "NONE", "NULL", "-", "—"}


def _basis(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in _UNSPECIFIED_BASIS_TOKENS:
        return UNSPECIFIED_BASIS
    return text

def _packaging(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in UNKNOWN_PACKAGING_TOKENS:
        return None
    return text

# Publisher spelling -> the chemical route graph's node code.
_FEEDSTOCK_FAMILIES = {
    "TOLUENE": "TOLUENE",
    "BENZENE": "BENZENE",
    "PURE BENZENE": "BENZENE",
    "CHLORINE": "CHLORINE",
    "LIQUID CHLORINE": "CHLORINE",
    "ANILINE": "ANILINE",
    "NITROBENZENE": "NITROBENZENE",
    "PROPYLENE": "PROPYLENE",
    "PROPYLENE OXIDE": "PO",
    "PO": "PO",
    "ETHYLENE": "ETHYLENE",
    "ETHYLENE OXIDE": "EO",
    "EO": "EO",
    "FORMALDEHYDE": "FORMALDEHYDE",
    "METHANOL": "METHANOL",
    "CAUSTIC SODA": "CAUSTIC_SODA",
    "NITRIC ACID": "NITRIC_ACID",
    "ADIPIC ACID": "ADIPIC_ACID",
    # Added 19 September 2026 with the SunSirs DEG series. Without it the family became the
    # publisher's spelling, "Diethylene glycol", and a second publisher spelling it "DEG" would
    # have opened a second identity for one chemical.
    "DIETHYLENE GLYCOL": "DIETHYLENE_GLYCOL",
    "DEG": "DIETHYLENE_GLYCOL",
    # The trade layer's polyester feedstocks (13 October 2026). MEG already had a display name under
    # ETHYLENE_GLYCOL; the publisher spelling would otherwise have minted "Ethylene glycol".
    "ETHYLENE GLYCOL": "ETHYLENE_GLYCOL",
    "MEG": "ETHYLENE_GLYCOL",
    "PHTHALIC ANHYDRIDE": "PHTHALIC_ANHYDRIDE",
    "MALEIC ANHYDRIDE": "MALEIC_ANHYDRIDE",
    "TEREPHTHALIC ACID": "TEREPHTHALIC_ACID",
    "PTA": "TEREPHTHALIC_ACID",
    "DIMETHYL TEREPHTHALATE": "DMT",
    "DMT": "DMT",
    "NAPHTHA": "REFINERY_NAPHTHA",
    "CRUDE OIL": "CRUDE_OIL",
}


# Publishers that spell the isocyanate out instead of abbreviating it. Longest first, so the
# hyphenated and qualified spellings are matched before the plain one.
_SPELLED_OUT = (
    ("METHYLENE DIPHENYLENE DIISOCYANATE", "MDI"),
    ("METHYLENE DIPHENYL DIISOCYANATE", "MDI"),
    ("DIPHENYLMETHANE DIISOCYANATE", "MDI"),
    ("TOLUYLENE DI-ISOCYANATE", "TDI"),
    ("TOLUENE DI-ISOCYANATE", "TDI"),
    ("TOLUYLENE DIISOCYANATE", "TDI"),
    ("TOLUENE DIISOCYANATE", "TDI"),
)


def _product_family(product: str) -> tuple[str, str | None]:
    p = product.strip().upper()
    # The rules below are written in the trade's abbreviations, and the fallback at the end of this
    # function returns the publisher's own spelling - so an unabbreviated name does not fail
    # loudly, it mints a family of its own. Procurement Resource prints "Toluene Diisocyanate", and
    # without this the TDI Germany feed registered on 5 November 2026 would have created a market
    # called exactly that, standing beside TDI and invisible on every page that shows TDI. The same
    # held for "Methylene Diphenyl Diisocyanate", which contains no "MDI" to match on.
    #
    # Rewritten to the abbreviation rather than answered here, so that everything downstream still
    # applies: "Polymeric Methylene Diphenyl Diisocyanate" becomes PMDI, not MDI.
    for spelled, short in _SPELLED_OUT:
        if spelled in p:
            p = p.replace(spelled, short)
            break
    if p in {"TDI 80/20", "TDI80/20", "TDI 8020"}:
        return "TDI", "TDI 80/20"
    if p.startswith("TDI"):
        return "TDI", None
    if "PMDI" in p or "POLYMERIC MDI" in p:
        # No grade. The family already says polymeric MDI, and this used to return whatever the
        # publisher called the product - so SCI99's "Polymeric MDI", Longzhong's "PMDI" and
        # ECHEMI's "PM200" became three grades and therefore three comparability groups and three
        # markets for one material. Migration 0028 merged them by hand and the next collection
        # made a fourth.
        return "PMDI", None
    if "PURE MDI" in p or "PUREMDI" in p:
        # Pure MDI is its own material and its own customer-facing chemical. Without this it fell
        # through to the family "MDI", which no page shows, so a pure MDI series would have been
        # collected into a market nobody can open. The store already holds five PURE_MDI markets
        # from the licensed history, and a collected series has to land on those, not beside them.
        return "PURE_MDI", None
    if "MDI" in p:
        return "MDI", None
    if "POLYOL" in p or "POLYETHER" in p or "POLYESTER" in p:
        # The FAMILY says which polyol it is; the grade stays empty.
        #
        # "POLYOL" used to be the answer for every one of them, and POLYOL is a group of materials
        # rather than a material - which put China's polyether series and FNAG's European polyether
        # purchases under two different family codes that both read "Polyether Polyol" on the
        # selector. Two series, one name, and no way for a reader to tell them apart.
        #
        # No grade, for the reason above: Longzhong began printing its polyol under a product name
        # on 16 September 2026 and three readings, including that day's price, left the series the
        # page shows. A grade taken off whatever a publisher typed is a new market every time
        # somebody edits a product page.
        if "SAN" in p or "GRAFT" in p or "POP" in p or "POLYMER POLYOL" in p:
            return "GRAFT_POLYOL", None
        if "PHD" in p:
            return "PHD_POLYOL", None
        if "PIPA" in p or "IPOL" in p:
            return "PIPA_POLYOL", None
        if "VISCO" in p or "VISKO" in p:
            return "VISCO_POLYOL", None
        if "POLYESTER" in p:
            return ("AROMATIC_POLYESTER_POLYOL" if "AROMATIC" in p
                    else "ALIPHATIC_POLYESTER_POLYOL"), None
        return "POLYETHER_POLYOL", None
    # Feedstocks. Named explicitly so the family is the graph's own code in upper case and stays
    # stable: the fallback below returns the publisher's spelling, and two publishers spelling a
    # chemical differently would mint two identities for one series.
    if p in _FEEDSTOCK_FAMILIES:
        return _FEEDSTOCK_FAMILIES[p], None
    return product.strip(), None

def data_grade(product: str) -> str | None:
    """A grade read off the product text, through the vocabulary rather than one rule of its own.

    It knew a single word, "slabstock". The vocabulary knows every spelling each of the twelve
    grades has actually been quoted as, so a publisher writing "high resilience" or "AA/BG" is
    placed instead of leaving the grade empty and the reading unplaceable.
    """
    text = str(product or "")
    for grade in chemical_vocabulary.GRADES:
        for spelling in (*grade.quoted_as, grade.display_name):
            if spelling and spelling.lower() in text.lower():
                return grade.code
    return None

def resolve_identity(data: dict) -> MarketIdentity:
    family, inferred_grade = _product_family(str(data["product"]))
    # The grade as the STORE keeps it: a code from the vocabulary, or nothing. A supplier's
    # product name resolves upward to the grade it is an example of, and commentary to nothing
    # (masterplan A, items 1-3). `ensure_identity` folds it again as the safety net for callers
    # that build an identity without coming through here, such as `app/bootstrap.py`.
    grade = chemical_vocabulary.grade_code_for_the_store(
        data.get("grade") or inferred_grade, family) or None
    geo = data.get("market_geography") or data.get("geography")
    basis = data.get("commercial_basis") or data.get("incoterm_or_commercial_basis")
    if basis is not None:
        basis = _basis(basis)
    packaging = _packaging(data.get("packaging"))
    currency = str(data.get("currency", "UNKNOWN")).upper()
    unit = canonical_unit(data.get("unit") or units.MT)
    # Whatever a connector calls the kind of price, the store keeps one of four (masterplan A,
    # item 6). `kind_of` folds the seven that used to be written; a name it does not know becomes
    # the headline kind rather than minting an eighth.
    observation_type = price_kinds.kind_of(data.get("observation_type") or price_kinds.HEADLINE)
    comp = data.get("comparability_group") or comparability_group(
        {"product_family": family, "product_grade_or_chemistry": grade, "market_geography": geo,
         "commercial_basis": basis, "packaging": packaging, "currency": currency, "unit": unit})
    return MarketIdentity(
        product_family=family,
        product_grade_or_chemistry=grade,
        origin=data.get("origin"),
        destination=data.get("destination"),
        market_geography=geo,
        incoterm_or_commercial_basis=basis,
        packaging=packaging,
        currency=currency,
        unit=unit,
        observation_type=observation_type,
        comparability_group=comp,
    )
