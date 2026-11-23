"""What each field of a market identity may hold, and what happens to a value that is not in it.

Masterplan D. A says what a market is; D is the same fault from the writing side - nothing stood
between a publisher's printed string and a new market. `ensure_identity` was get-or-create on nine
fields, the document extractor handed it whatever the page said, and a market appeared.

Three questions had to be answered separately, and the plan's draft ran them together:

    1. does this value mean the same as one we already hold?   -> FOLD    (a spelling)
    2. is this a value this field may hold at all?             -> GATE    (a stranger)
    3. what happens to a reading we will not file?             -> QUARANTINE

Most of the folds were built with A: the unit folds every tonne spelling to `MT` (0057), the price
kind folds seven values to four (0058), the grade resolves a product name upward to the grade it is
an example of and commentary to nothing (0060), packaging folds seven spellings of silence to one,
and `national` folds a province to its country. What did not exist is the **gate** and the
**quarantine**, and that is what this module is.

**Three fields are gated, not six.** The plan's remedy table listed six, and four of those already
have a ruling that says fold rather than refuse - each one made on evidence, and none of them is
overturned here:

    geography   NOT gated. Stefan, 13 October 2026, on the customer pages: any geography the store
                prices is offered, because "a fixed list hid every country nobody had typed into it
                yet". A gate here is that same list, one step earlier.
    grade       NOT gated. A, items 1-3: a spelling the vocabulary does not know resolves to
                NOTHING, which leaves the market intact and costs only the grade.
    terms       NOT gated. `canonical_basis` folds wording it cannot read to UNSPECIFIED and the
                publisher's own words survive in the quote. Refusing a good price because the terms
                line read "delivery: Linyi; formal Incoterm n/s" would throw away the price.
    packaging   NOT gated. A packaging that is stated and new - an IBC, a flexibag - is a market we
                do not hold yet, not junk. Only the spellings of silence fold.

    family      GATED. The one field with no fold available: nothing can tell what "MDI" means
                where the store keeps PMDI and PURE_MDI apart, and nothing should guess.
    unit        GATED. `canonical_unit` passes an unrecognised token through, deliberately, "so a
                connector sending something new is visible in the store rather than silently
                relabelled MT". Visible in the store means a market was minted: 23 identities held
                a PRICE in the unit field ("18,166.67/mt"), one print as two markets with one
                reading each. Visible in the quarantine is what that docstring wanted.
    price kind  GATED. `kind_of` falls back to the headline kind for a value it does not know,
                "because a reading with an odd label is still a price and dropping it would be
                worse than filing it as an assessment". Both halves of that were true while the
                only alternative was dropping it: a new publisher sending `offer_price` would be
                filed as `assessed` and reach a customer page as a market assessment. The
                quarantine is the third option, and it is better than either.

So the gate refuses only where there is no honest fold AND filing it anyway would publish something
untrue. `kind_of` keeps its forgiving fallback, because every reader calls it on values already in
the store; the writer calls `resolve_price_kind` here.

**A refusal is not a loss.** The reading goes to `quarantined_reading` with the publisher's own
words, the document it came from and the field that stopped it, and Data & Sources shows it. Naming
the value is then a one-line change and the reading arrives on the next collection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raw_material_forecasting import price_kinds, units
from raw_material_forecasting.chemicals import canonical_code

# Every family the application holds a price for, and the one place the list lives.
#
# It was in `market_selection_ui.py` at the repository root, which the engine cannot import, so the
# engine had no way to ask the question at the door - `is_offered_chemical` asked it on the way out,
# where the market had already been minted. Moved here, in `src/`, which both sides import;
# `market_selection_ui` reads these.
#
# The polyols are named one by one rather than as "Polyol": different feedstocks, different foams,
# different prices, and a buyer of HR polyol is not shopping for polyester (Stefan, 17 September
# 2026).
RAW_MATERIALS: tuple[str, ...] = (
    "TDI", "PMDI", "PURE_MDI", "PO",
    "POLYETHER_POLYOL", "RIGID_POLYOL", "HR_POLYOL", "GRAFT_POLYOL", "PHD_POLYOL", "PIPA_POLYOL",
    "VISCO_POLYOL",
    "ALIPHATIC_POLYESTER_POLYOL", "AROMATIC_POLYESTER_POLYOL", "FATTY_ACID_ESTER_POLYOL",
)

# HDI and IPDI are NOT here, and were taken out on 6 November 2026. Stefan: "Delete both HDI and
# IPDI out of the chemicals to track, etc. We do not need them." Written down because they were
# added on 28 October and the thing that added them will happen again - two readings arrive in a
# document import, no page can name the family, and naming it looks like the obvious repair.
#
# They are aliphatic isocyanates, and an aliphatic isocyanate is a COATINGS material. It is used
# where sunlight would yellow an aromatic one: automotive clearcoat and refinish, industrial and
# wood coatings. It is rarely sold as the monomer at all - the trade is in the trimer and the
# biuret. HTC's book is foam. The Command Center's fourteen company categories are Foam
# Manufacturer, System House, PU Material Distributor, End-User of PU materials and the rest of
# that chain, and not one of them is a coatings category.
#
# So Stefan's standing rule of 26 September - "the more chemicals we can add that are PU Raw
# Materials the better" - does not reach them. It is about the materials his customers buy, and
# these are PU raw materials his customers do not buy. Their cost chain says the same thing twice
# over: HDI runs back through hexamethylenediamine to adiponitrile and butadiene, IPDI through
# isophorone diamine. Neither touches toluene, benzene, aniline or chlorine, so neither takes any
# forecasting leverage from what this application already holds, and neither gives any back.
#
# Nothing is deleted from the store by this. The two readings each stay where they are; they come
# off Current Market, and Data & Sources says "Chemical not named in the application", which is
# the honest answer rather than a fault. A reading arriving after this waits in the quarantine.
#
# WHAT WOULD BRING THEM BACK, and it is one line: light-stable integral skin - steering wheels,
# armrests, soles that must not yellow - is foam, and it is made with an aliphatic isocyanate. A
# system-house customer asking about yellowing is the signal. A document import is not.

# What the raw materials are made from. Collected because they explain the raw materials; a foam
# producer does not buy them (Stefan, 1 October 2026).
FEEDSTOCKS: tuple[str, ...] = (
    "BENZENE", "TOLUENE", "EO", "DIETHYLENE_GLYCOL", "ADIPIC_ACID", "ANILINE",
    "ETHYLENE_GLYCOL", "PHTHALIC_ANHYDRIDE", "MALEIC_ANHYDRIDE", "TEREPHTHALIC_ACID", "DMT",
)

PRICED_FAMILIES: frozenset[str] = frozenset(
    canonical_code(code) for code in (*RAW_MATERIALS, *FEEDSTOCKS))

# Families that are real materials and still refused, because the name does not say which market.
#
# These are checked AFTER `canonical_code`, so a word that folds to a priced family never reaches
# them. `MDI` and `POLYOL` were both listed here when this was written and both were wrong to be:
# `POLYOL` has folded to POLYETHER_POLYOL since migration 0035, so the entry was unreachable, and
# `MDI` folds to PMDI by Stefan's ruling of 28 October 2026 (see `chemicals.LEGACY_CODES`, where the
# reasoning and the evidence are). What is left is the words that name a class rather than a
# material, for which there is no fold to make.
AMBIGUOUS_FAMILIES: dict[str, str] = {
    # HDI and IPDI came out of this sentence with themselves, 6 November 2026: telling a
    # publisher to name a chemical the next gate refuses is worse than saying nothing.
    "ISOCYANATE": "name the isocyanate: TDI, PMDI or pure MDI",
    "POLYURETHANE": "a finished polymer, not a raw material we price",
    "MDI_PMDI": "one reading cannot be two markets",
}

FAMILY, UNIT, PRICE_KIND = "product_family", "unit", "observation_type"
GATED: tuple[str, ...] = (FAMILY, UNIT, PRICE_KIND)


@dataclass(frozen=True)
class Refusal:
    """One field, the publisher's own word for it, and why it did not get through."""

    field: str
    value: str
    reason: str

    def said(self) -> str:
        """One line for a table: what was read, and why it is not a market."""
        shown = self.value if self.value else "(nothing)"
        return f"{self.field} = {shown!r}: {self.reason}"


def is_priced_family(value: Any) -> bool:
    """Whether this is a family the application holds a price for."""
    return canonical_code(value) in PRICED_FAMILIES


def resolve_family(value: Any) -> str | Refusal:
    code = canonical_code(value)
    if code in PRICED_FAMILIES:
        return code
    if not code:
        return Refusal(FAMILY, "", "no chemical was read from the document")
    if code in AMBIGUOUS_FAMILIES:
        return Refusal(FAMILY, str(value), AMBIGUOUS_FAMILIES[code])
    return Refusal(FAMILY, str(value),
                   "not a chemical this application prices; name it in "
                   "`identity_vocabulary.RAW_MATERIALS` or `FEEDSTOCKS` and it arrives on the next "
                   "collection")


def resolve_unit(value: Any) -> str | Refusal:
    """`MT` for every spelling of a tonne, "" for silence, a refusal for anything else.

    Silence is allowed and is not a refusal: `canonical_unit` has always stored a missing unit as
    missing, and the screen is where a price per nothing becomes a price per MT (`display_unit`).
    Whether that assumption should be made at all is section E's question, not this one's.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    folded = units.canonical_unit(text)
    if folded == units.MT:
        return folded
    if any(character.isdigit() for character in text):
        return Refusal(UNIT, text,
                       "a price, not a unit - the row was read wrongly, so its price cannot be "
                       "trusted either")
    return Refusal(UNIT, text, f"not a unit this application prices in; one token, {units.MT}")


def resolve_price_kind(value: Any, source_id: Any = "") -> str | Refusal:
    """One of the four kinds, or a refusal. The writer's version of `price_kinds.kind_of`.

    `kind_of` is forgiving on purpose and every reader needs it to be - it is called on values
    already in the store, and A folded seven old spellings into four through it. At the door,
    forgiveness means a label nobody recognises is filed as an assessment and published as one.
    """
    text = str(value or "").strip().lower()
    if not text:
        # Silence means the headline kind, which is what the application has always read it as -
        # `PUBLISHED_OBSERVATION_TYPE` on the customer pages does the same.
        return price_kinds.HEADLINE
    if text in price_kinds.KINDS or text in price_kinds.WAS:
        return price_kinds.kind_of(text, source_id)
    return Refusal(PRICE_KIND, str(value),
                   "not one of the four kinds of price (assessed, contract, deal, ask), and "
                   "filing it as an assessment would publish it as a market price")


def refusals(fields: dict[str, Any], *, source_id: Any = "") -> tuple[Refusal, ...]:
    """Every gate this identity fails, in field order. Empty when it may be written.

    All of them rather than the first, because a document read wrongly usually fails more than one
    and a reader fixing them one collection at a time learns that the hard way.
    """
    answers = (
        resolve_family(fields.get(FAMILY)),
        resolve_unit(fields.get(UNIT)),
        resolve_price_kind(fields.get(PRICE_KIND), source_id),
    )
    return tuple(one for one in answers if isinstance(one, Refusal))


def resolved(fields: dict[str, Any], *, source_id: Any = "") -> dict[str, Any]:
    """The gated fields as the store should keep them. Raises nothing - call `refusals` first."""
    out = dict(fields)
    for field, answer in (
            (FAMILY, resolve_family(fields.get(FAMILY))),
            (UNIT, resolve_unit(fields.get(UNIT))),
            (PRICE_KIND, resolve_price_kind(fields.get(PRICE_KIND), source_id))):
        if not isinstance(answer, Refusal):
            out[field] = answer
    return out
