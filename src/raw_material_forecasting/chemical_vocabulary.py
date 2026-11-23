"""Family, grade, product - the three levels a chemical is named at, and the only list of them.

Masterplan A, items 1, 2 and 3. Stefan, 27 October 2026: *"the product name is not the chemical
name... more like an example of the chemical."*

    level     what it is                                     carries a price?
    family    the chemical class the market trades            yes - most markets are priced here
    grade     family + the technical fingerprint the trade    yes, where a publisher quotes the tier
              distinguishes
    product   one supplier's actual product, an EXAMPLE of    no - a product name is not a
              a grade                                         chemical name

The store's `product_grade_or_chemistry` field held **64 values** and was all three levels at once,
plus commentary. What each one is was not a judgement call in the end: **the publisher who wrote it
says which level it is**, and that is what this module is built on.

    source                      what its grade values are
    fnag_switzerland            supplier PRODUCTS - HTC's own purchase records, 20 of them
    icis_history                published GRADE tiers - the tier the publisher quotes
    longzhong_polyester         chemistry GRADES - which glycol the polyester is built on
    foampartner_china           a property tier and an isomer grade
    100ppi / chemicalbook       one product, MN-3050D
    import_* (hand imports)     COMMENTARY - "grade unspecified", "MW/f n/s", "source notation"

**CAS is a property, never a key** (item 2). Measured across the 324 TDS/SDS files, it fails in
both directions: polyether polyol is written `9082-00-2`, `9003-11-6`, `9004-73-3`, `25791-96-2`
and `68936-80-1` depending on which supplier wrote the sheet, and HR, visco, graft, PHD and PIPA
polyol each carry one of those same numbers although HTC prices them separately. Nine of 22 sheets
state a composition rather than a compound - Shell Caradol SP30-47 is `9082-00-2` at 50-60% plus
`57913-80-1` at 40-50%. So a chemical is identified by its code; CAS is zero, one or several
numbers, each with the role it plays.

Nothing here is invented. Every grade's fingerprint is a number the suppliers' own sheets print -
OH value or MW and functionality for the base polyols, solids content for graft, PHD and PIPA, the
isomer ratio for TDI, functionality and viscosity for crude MDI and pMDI - and every product is one
HTC has actually bought or been quoted, with the publisher that recorded it named.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FAMILY, GRADE, PRODUCT = "family", "grade", "product"
LEVELS: tuple[str, ...] = (FAMILY, GRADE, PRODUCT)

# What a CAS number is doing on a sheet. A composition states several, and the role is the only way
# to tell the main substance from what it is blended with.
PRIMARY = "primary"            # the substance the sheet is about
COMPONENT = "component"        # one constituent of a stated composition
REGISTERED_AS = "registered_as"  # the same substance registered a second time
ROLES: tuple[str, ...] = (PRIMARY, COMPONENT, REGISTERED_AS)


@dataclass(frozen=True)
class CasNumber:
    """One CAS number on a chemical: the number, what it is doing there, and how much of it."""

    number: str
    role: str = PRIMARY
    # The share stated on the sheet, as a fraction, where the sheet gives a range or a figure.
    # None where it states none, which is most of them - absence is not zero.
    share: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"{self.role!r} is not a CAS role; one of {ROLES}")


@dataclass(frozen=True)
class Fingerprint:
    """The numbers the trade compares two grades of one family by.

    Read off the suppliers' own sheets, not invented. Every field is optional because a family is
    distinguished by some of them and not others: a polyol by OH value and functionality, a TDI by
    its isomer ratio, a pMDI by functionality and viscosity.
    """

    oh_value: float | None = None          # mg KOH/g
    molecular_weight: float | None = None
    functionality: float | None = None
    solids_content: tuple[float, float] | None = None   # graft, PHD, PIPA - a stated range
    isomer_ratio: str = ""                 # TDI: "80/20", "65/35"
    viscosity: float | None = None         # mPa.s, for crude MDI and pMDI
    note: str = ""

    @property
    def stated(self) -> tuple[str, ...]:
        """Which of the numbers this fingerprint actually carries."""
        return tuple(name for name, value in (
            ("oh_value", self.oh_value), ("molecular_weight", self.molecular_weight),
            ("functionality", self.functionality), ("solids_content", self.solids_content),
            ("isomer_ratio", self.isomer_ratio or None), ("viscosity", self.viscosity),
        ) if value is not None)

    @property
    def is_empty(self) -> bool:
        return not self.stated


@dataclass(frozen=True)
class Grade:
    """A grade: a family plus the fingerprint that distinguishes it, and nothing else.

    `code` is what the store holds. `quoted_as` is every spelling a publisher has used for it, so
    a reading can be placed without the spelling becoming a market of its own.
    """

    code: str
    family: str
    display_name: str
    fingerprint: Fingerprint = field(default_factory=Fingerprint)
    quoted_as: tuple[str, ...] = ()
    cas_numbers: tuple[CasNumber, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class SupplierProduct:
    """One supplier's product: an example of a grade, never a market of its own.

    Item 3. Stefan ruled on 27 October 2026 that a product name is not a chemical name, so these
    live in a register keyed to the grade they are an example of rather than being folded into the
    grade field. Two products of one grade can differ in their numbers without either being wrong,
    which an alias list cannot express.
    """

    product_name: str
    family: str
    supplier: str = ""
    grade_code: str = ""
    fingerprint: Fingerprint = field(default_factory=Fingerprint)
    # Which publisher's record this product was read from. Every one of these is a product HTC has
    # bought or been quoted, not a name from a catalogue.
    recorded_by: str = ""
    note: str = ""


# --------------------------------------------------------------------------- CAS, as evidence

# The measured multi-CAS cases from the 324-sheet scan. A family not named here keeps whatever
# single number the route-graph seed carries, read as one `primary` entry.
CAS_EVIDENCE: dict[str, tuple[CasNumber, ...]] = {
    # One family, five numbers, depending on which supplier wrote the sheet. The last two are the
    # same substance registered twice, which is why the role says so.
    "POLYETHER_POLYOL": (
        CasNumber("9082-00-2"), CasNumber("9003-11-6", REGISTERED_AS),
        CasNumber("9004-73-3", REGISTERED_AS), CasNumber("25791-96-2", REGISTERED_AS),
        CasNumber("68936-80-1", REGISTERED_AS),
    ),
    # A composition, not a compound: Shell Caradol SP30-47 states both, with shares.
    "GRAFT_POLYOL": (
        CasNumber("9082-00-2", COMPONENT, (0.50, 0.60)),
        CasNumber("57913-80-1", COMPONENT, (0.40, 0.50)),
    ),
    # Crude MDI is the polymeric mixture with the monomer in it.
    "PMDI": (CasNumber("9016-87-9"), CasNumber("101-68-8", COMPONENT)),
    # These are built on the same polyether polyol, which is exactly why CAS cannot identify them.
    "HR_POLYOL": (CasNumber("9082-00-2", COMPONENT),),
    "VISCO_POLYOL": (CasNumber("9082-00-2", COMPONENT),),
    "PHD_POLYOL": (CasNumber("9082-00-2", COMPONENT),),
    "PIPA_POLYOL": (CasNumber("9082-00-2", COMPONENT),),
}

# Two of the 22 have no number at all by nature and stay that way; four more are marked TO VERIFY
# in `Claude outputs/Chemicals without a CAS number 2026-10-27.xlsx` and are not written into the
# vocabulary until a register check settles them. Refinery naphtha is a cut, not a substance.
NO_SINGLE_CAS: frozenset[str] = frozenset({
    "DIMETHYLOLBUTANAL", "PYROLYSIS_GASOLINE", "REFINERY_NAPHTHA",
})

_CAS = re.compile(r"^\d{2,7}-\d{2}-\d$")


def valid_cas(number: Any) -> bool:
    """The format, and the check digit the format carries."""
    text = str(number or "").strip()
    if not _CAS.match(text):
        return False
    body, check = text.replace("-", "")[:-1], int(text[-1])
    return sum(int(d) * i for i, d in enumerate(reversed(body), start=1)) % 10 == check


def cas_numbers_of(family: Any, seeded: Any = "") -> tuple[CasNumber, ...]:
    """Every CAS number for a family: the measured evidence, else the seed's single number.

    The seed carries one optional string per node. Where the scan found a family writes itself
    several ways, or states a composition, `CAS_EVIDENCE` has the list and it wins - the seed's one
    number is in it.
    """
    code = str(family or "").strip().upper()
    if code in CAS_EVIDENCE:
        return CAS_EVIDENCE[code]
    one = str(seeded or "").strip()
    return (CasNumber(one),) if one else ()


def families_of_cas(number: Any) -> tuple[str, ...]:
    """Every family that carries this CAS number. Many-to-many in both directions, as measured."""
    wanted = str(number or "").strip()
    return tuple(sorted(code for code, numbers in CAS_EVIDENCE.items()
                        if any(entry.number == wanted for entry in numbers)))


# --------------------------------------------------------------------------- the grades

# A grade is a family plus a fingerprint. `quoted_as` holds the spellings the store actually
# received, each with the publisher that wrote it, so nothing here is a guess about what a
# publisher meant.
GRADES: tuple[Grade, ...] = (
    # --- TDI: the isomer ratio, which is the whole of the distinction.
    Grade("TDI_80_20", "TDI", "TDI 80/20", Fingerprint(isomer_ratio="80/20"),
          quoted_as=("TDI 80/20", "80/20"),
          cas_numbers=(CasNumber("26471-62-5"),),
          note="Stefan's own example of a case where one CAS is the whole story."),
    Grade("TDI_65_35", "TDI", "TDI 65/35", Fingerprint(isomer_ratio="65/35"),
          quoted_as=("TDI 65/35", "65/35"),
          note="fnag_switzerland and foampartner_china, 75 readings."),
    # --- the published polyether tiers. ICIS quotes the application tier, which is a real grade:
    # a slabstock polyol and a rigid polyol are not interchangeable at any price.
    Grade("FLEXIBLE_SLABSTOCK_POLYETHER", "POLYETHER_POLYOL", "Flexible slabstock polyether",
          Fingerprint(oh_value=56.0, functionality=3.0, note="the trade's slabstock triol"),
          quoted_as=("flexible slabstock", "slabstock", "soft foam", "soft-foam polyether"),
          note="icis_history, 2,050 readings - the largest grade in the store."),
    Grade("RIGID_POLYETHER", "RIGID_POLYOL", "Rigid polyether polyol",
          Fingerprint(oh_value=440.0, functionality=4.3,
                      note="high OH, high functionality - the rigid-foam tier"),
          quoted_as=("rigid", "rigid, polyether", "rigid polyether"),
          note="icis_history, 732 readings over three spellings of one tier."),
    Grade("HIGH_RESILIENCE_POLYETHER", "HR_POLYOL", "High-resilience polyether",
          Fingerprint(oh_value=28.0, functionality=3.0),
          quoted_as=("high resilience", "HR"),
          note="icis_history, 577 readings."),
    # --- graft polyol: the solids content, which is what a buyer compares.
    Grade("GRAFT_POLYOL_43_45", "GRAFT_POLYOL", "Graft polyol 43-45% solids",
          Fingerprint(solids_content=(0.43, 0.45)),
          quoted_as=("43-45% polymer", "43-45%"),
          note="icis_history, 307 readings."),
    # --- MDI: the two fractions the trade actually separates.
    Grade("PURE_MONOMERIC_MDI", "PURE_MDI", "Pure (monomeric) MDI",
          Fingerprint(functionality=2.0, note="the distilled monomeric fraction"),
          quoted_as=("pure (monomeric)", "pure", "monomeric"),
          cas_numbers=(CasNumber("101-68-8"),),
          note="icis_history, 2,122 readings."),
    Grade("CRUDE_POLYMERIC_MDI", "PMDI", "Crude polymeric MDI",
          Fingerprint(functionality=2.7, viscosity=200.0),
          quoted_as=("polymeric", "crude / polymeric", "polymeric MDI", "crude"),
          note="icis_history, 3,390 readings over two spellings; a composition, see CAS_EVIDENCE."),
    # --- the aliphatic polyesters: which glycol the polyester is built on. A real chemistry
    # distinction, and the one case where the publisher's own notation IS the fingerprint.
    Grade("POLYESTER_AA_EG", "ALIPHATIC_POLYESTER_POLYOL", "Adipic acid / ethylene glycol polyester",
          Fingerprint(note="adipic acid esterified with ethylene glycol"),
          quoted_as=("AA/EG", "AA/EG (source notation)",
                     "AA/EG (adipic acid / ethylene glycol); MW/f n/s"),
          note="longzhong_polyester."),
    Grade("POLYESTER_AA_BG", "ALIPHATIC_POLYESTER_POLYOL", "Adipic acid / butanediol polyester",
          Fingerprint(note="adipic acid esterified with 1,4-butanediol"),
          quoted_as=("AA/BG", "AA/BG (source notation)",
                     "AA/BG (adipic acid / butanediol); MW/f n/s"),
          note="longzhong_polyester."),
    Grade("POLYESTER_AA_BG_EG", "ALIPHATIC_POLYESTER_POLYOL",
          "Adipic acid / blended glycol polyester",
          Fingerprint(note="a blended butanediol and ethylene glycol feed"),
          quoted_as=("AA/BG+EG", "AA/BG+EG (source notation)",
                     "AA/BG+EG (blended glycol feed); MW/f n/s"),
          note="longzhong_polyester."),
    Grade("POLYESTER_LOW_FOGGING", "ALIPHATIC_POLYESTER_POLYOL", "Low-fogging polyester polyol",
          Fingerprint(note="a stated fogging limit, which is the tier this publisher quotes"),
          quoted_as=("low fogging",),
          note="foampartner_china, 41 readings."),
)

# A grade's own family restated adds nothing: the family field already says it. These spellings are
# the family and not a grade, and the grade field holding them is how `PHD_POLYOL` came to have the
# grade "PHD".
RESTATES_THE_FAMILY: dict[str, str] = {
    "PHD": "PHD_POLYOL",
    "PIPA": "PIPA_POLYOL",
}

# Not a grade at all: the publisher stated no grade and the extractor wrote down what it saw, or a
# note about the reading. These go to section D's quarantine, not into the vocabulary.
IS_COMMENTARY: frozenset[str] = frozenset({
    "grade unspecified",
    "grade unspecified (80/20 unconfirmed)",
    "grade unspecified; pure/pMDI n/s",
    "grade/MW/functionality unspecified",
    "table family/grade unspecified",
    "rigid, chemistry unconfirmed",
    "soft foam, higher price group; MW n/s",
    "soft foam, lower price group; MW n/s",
    "soft foam; MW/functionality n/s",
    "soft-foam polyether; MW/functionality n/s",
    "polymeric; composite, supplier grades mixed",
    "PM200; Qualified",
    "content >=99.5%; domestic origin",
    "Wanhua-origin; national standard, 200-kg pack",
    "PM200; domestic-origin",
    "PM200; Wanhua-origin",
    "PM100, stated purity 99.6%",
})


# --------------------------------------------------------------------------- the product register

# Every product HTC has actually bought or been quoted, with the publisher that recorded it. The
# 324 TDS/SDS files on Stefan's computer are the rest of this register's content; these are the ones
# the STORE already holds, which is what item 3 has to get out of the grade field.
PRODUCTS: tuple[SupplierProduct, ...] = (
    # --- HTC's own purchase records. `fnag_switzerland` is the purchase lane, so every grade value
    # it wrote is a product: these are the things HTC buys.
    SupplierProduct("CP 1050", "POLYETHER_POLYOL", grade_code="FLEXIBLE_SLABSTOCK_POLYETHER",
                    recorded_by="fnag_switzerland",
                    note="Stefan's own example of a product rather than a chemical."),
    SupplierProduct("CP 1421", "POLYETHER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("CP 4610", "POLYETHER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("C 5710", "POLYETHER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("Aktiv 35", "POLYETHER_POLYOL", recorded_by="fnag_switzerland",
                    note="Stefan's own example."),
    SupplierProduct("Soft 38", "POLYETHER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("OHZ 48", "POLYETHER_POLYOL", fingerprint=Fingerprint(oh_value=48.0),
                    recorded_by="fnag_switzerland",
                    note="the name states the OH value, so the product carries it as its own."),
    SupplierProduct("PO 56", "POLYETHER_POLYOL", fingerprint=Fingerprint(oh_value=56.0),
                    recorded_by="fnag_switzerland",
                    grade_code="FLEXIBLE_SLABSTOCK_POLYETHER",
                    note="OH 56 is the slabstock triol, which is the grade it is an example of."),
    SupplierProduct("SAN 43", "GRAFT_POLYOL", grade_code="GRAFT_POLYOL_43_45",
                    fingerprint=Fingerprint(solids_content=(0.43, 0.43)),
                    recorded_by="fnag_switzerland",
                    note="Stefan's own example; the 43 in the name is the solids content."),
    SupplierProduct("SAN 10", "GRAFT_POLYOL", fingerprint=Fingerprint(solids_content=(0.10, 0.10)),
                    recorded_by="fnag_switzerland",
                    note="a second product of the same family at a different solids content - "
                         "two products of one family differing in their numbers, which is exactly "
                         "what an alias list could not express."),
    SupplierProduct("Priplast 3186", "FATTY_ACID_ESTER_POLYOL", recorded_by="fnag_switzerland",
                    note="Stefan's own example."),
    SupplierProduct("Ester 60", "ALIPHATIC_POLYESTER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("Ester 213", "ALIPHATIC_POLYESTER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("PU 1431", "ALIPHATIC_POLYESTER_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("EP 8262.03", "VISCO_POLYOL", recorded_by="fnag_switzerland"),
    SupplierProduct("Visko R", "VISCO_POLYOL", recorded_by="fnag_switzerland"),
    # --- products named in a hand import or a quote, with the supplier the record states.
    SupplierProduct("Lupranate MX118/1", "MDI", supplier="BASF",
                    recorded_by="import_htc_global",
                    note="holds no reading: a quote recorded, not a priced market."),
    SupplierProduct("W8223", "MDI", supplier="Wanhua", recorded_by="import_htc_global",
                    note="via Nagase; holds no reading."),
    SupplierProduct("M-200", "PMDI", supplier="Wanhua", grade_code="CRUDE_POLYMERIC_MDI",
                    recorded_by="import_echemi",
                    note="Wanhua's pMDI grade name; 'M-200 model' is the same product."),
    SupplierProduct("MN-3050D", "POLYETHER_POLYOL", fingerprint=Fingerprint(molecular_weight=3000.0),
                    recorded_by="100ppi_polyol_mw3000",
                    note="the two seller-quote lanes; MW 3000 is in the lane's own name."),
    SupplierProduct("4110", "POLYETHER_POLYOL", recorded_by="import_chemicalbook",
                    note="a Chinese domestic product code; 'type 4110' is the same product."),
)

# A product written more than one way in the store. The register holds one product; these are the
# other spellings of its name, so a reading can be placed without minting anything.
PRODUCT_SPELLINGS: dict[str, str] = {
    "m-200 model": "M-200",
    "type 4110": "4110",
    "4110; mw/functionality n/s": "4110",
    "type 4110; mw/functionality n/s": "4110",
    "lupranate mx118/1 / basf": "Lupranate MX118/1",
    "w8223 / wanhua (via nagase)": "W8223",
    "mn-3050d soft-foam polyether polyol": "MN-3050D",
}


# --------------------------------------------------------------------------- resolving a value

QUARANTINE = "quarantine"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).lower()


_BY_SPELLING: dict[str, Grade] = {}
for _grade in GRADES:
    _BY_SPELLING[_norm(_grade.code)] = _grade
    _BY_SPELLING[_norm(_grade.display_name)] = _grade
    for _spelling in _grade.quoted_as:
        _BY_SPELLING[_norm(_spelling)] = _grade

_BY_PRODUCT: dict[str, SupplierProduct] = {_norm(p.product_name): p for p in PRODUCTS}
for _written, _is in PRODUCT_SPELLINGS.items():
    _found = _BY_PRODUCT.get(_norm(_is))
    if _found is not None:
        _BY_PRODUCT[_norm(_written)] = _found


def grade_of(value: Any, family: Any = "") -> Grade | None:
    """The grade this spelling names, or None. A grade of another family is not this one's."""
    found = _BY_SPELLING.get(_norm(value))
    if found is None:
        return None
    wanted = str(family or "").strip().upper()
    return found if not wanted or found.family == wanted else None


def product_of(value: Any, family: Any = "") -> SupplierProduct | None:
    """The supplier product this spelling names, or None."""
    found = _BY_PRODUCT.get(_norm(value))
    if found is None:
        return None
    wanted = str(family or "").strip().upper()
    return found if not wanted or found.family == wanted else None


def resolve(value: Any, family: Any = "") -> tuple[str, Any]:
    """What a stored grade value actually is, as (level, what).

    Returns one of:
        ("grade", Grade)         a grade of this family
        ("product", Product)     a supplier's product - an example of a grade, never a market
        ("family", code)         the family restated, which adds nothing
        ("quarantine", reason)   commentary, or a spelling nothing recognises

    Nothing is invented: a spelling this does not know goes to quarantine rather than becoming a
    grade, because a grade minted from whatever a publisher typed is a market minted from a typo.
    """
    text = str(value or "").strip()
    if not text:
        return (FAMILY, str(family or "").strip().upper())
    found = grade_of(text, family)
    if found is not None:
        return (GRADE, found)
    product = product_of(text, family)
    if product is not None:
        return (PRODUCT, product)
    restates = RESTATES_THE_FAMILY.get(text)
    if restates and restates == str(family or "").strip().upper():
        return (FAMILY, restates)
    if text in IS_COMMENTARY:
        return (QUARANTINE, "the publisher stated no grade; this is a note about the reading")
    return (QUARANTINE, f"{text!r} is not a grade, a product or a family this vocabulary knows")


def grade_code_for_the_store(value: Any, family: Any = "") -> str:
    """What the store's grade field should hold for this value: a grade code, or nothing.

    A product resolves UPWARD to the grade it is an example of where the register names one, and to
    nothing where it does not - a product is never what the grade field holds. The family restated,
    and commentary, are nothing too.
    """
    level, what = resolve(value, family)
    if level == GRADE:
        return what.code
    if level == PRODUCT:
        return what.grade_code or ""
    return ""
