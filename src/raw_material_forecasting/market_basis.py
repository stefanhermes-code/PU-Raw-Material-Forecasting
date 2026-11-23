"""What a price was quoted on: a delivery term, or the kind of quotation it is.

The store calls this dimension `commercial_basis` and the comparability group calls it `basis`. The
screens called it "Incoterm", which is true of some of what it holds and false of the rest: measured
28 October 2026 over the store's 343 identities, it holds ten values, seven of them delivery terms
(CIF 110, FOB 101, DAP 35, CFR 23, EXW 21, DDP 4, FD 4) and three of them not (REGIONAL_MARKET 31,
UNSPECIFIED 13, CHINA_DOMESTIC_SELLER_QUOTE 1). A producer's list price is not a delivery term, and
neither is a regional market assessment or a domestic seller's quote.

The codes are keys. `family|grade|geography|basis|packaging|unit|kind` is what decides whether two
readings are the same series (seven fields since migration 0059 took the currency out), so a code is
never renamed to read better; it is translated here, at the edge, on its way to a screen.
"""
from __future__ import annotations

from typing import Any

# Written out rather than abbreviated only where the abbreviation is not the trade's own word. EXW,
# FOB, CFR, CIF, DDP, FCA and DAP are what a buyer says out loud and are left exactly as they are.
BASIS_NAMES: dict[str, str] = {
    # No Incoterm known: "Unspecified" (Stefan, 22 September 2026). "Delivered" does not say DAP or
    # DDP.
    "DELIVERED": "Unspecified",
    # A regional assessment states no delivery term, which is not the same as no term being known.
    # Calling it "Unspecified" while the chart legend printed the raw REGIONAL_MARKET beside it put
    # one thing on one screen under two names (Stefan, 26 September 2026).
    "REGIONAL_MARKET": "Regional assessment",
    # The geography is its own selector beside this one, so the "China" in the code would print the
    # country twice on one row.
    "CHINA_PRODUCER_LIST": "Producer list price",
    "CHINA_PRODUCER_EXW_CASH": "Producer EXW, cash",
    "CHINA_PRODUCER_EXW_ACCEPTANCE": "Producer EXW, acceptance",
    "CHINA_PRODUCER_DELIVERED_CASH": "Producer delivered, cash",
    "CHINA_DOMESTIC_SELLER_QUOTE": "Domestic seller quote",
}

# A publisher that states no terms at all. Saying so is the honest reading; "Not specified" alone
# reads as though somebody forgot to fill a field in.
UNSTATED = {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED", "NONE"}

# The eleven Incoterms 2020, and the two older ones still printed by publishers who have not moved
# on. A delivery term that is not one of these, and is not one of the price bases named above, is
# not a delivery term - it is a sentence about one.
INCOTERMS: frozenset[str] = frozenset({
    "EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF",
    "DAT", "DDU",
})

# Every term that names a delivery, which is the Incoterms plus the ones publishers print that are
# not Incoterms at all.
#
# **Three lists answered this question and disagreed** (found while applying masterplan C, settled
# with F): `INCOTERMS` above has DAT and DDU but not FD; `market_selection_ui.INCOTERMS` had FD but
# not DPU, CIP or CPT; `platform_admin.INCOTERM_OPTIONS` was a third. **FD was an Incoterm to one of
# them and not to the other two, while four identities carry it** - the ICIS Western Europe history,
# 1,385 readings of TDI, pMDI, pure MDI and polyether polyol, which reached no screen at all until
# FD was added to the selector's list and not to the others.
#
# FD ("free delivered") and DEL are not Incoterms and never were. They are delivery terms, which is
# the question a page actually asks, so this is the list the pages read and `INCOTERMS` stays what
# its name says: the codes that are Incoterms.
NOT_AN_INCOTERM_BUT_A_DELIVERY_TERM: frozenset[str] = frozenset({"FD", "DEL"})
DELIVERY_TERMS: frozenset[str] = INCOTERMS | NOT_AN_INCOTERM_BUT_A_DELIVERY_TERM

UNSPECIFIED = "UNSPECIFIED"

# What a publisher writes instead of the three letters. Matched on the words of the label, so
# "cash ex works; VAT included" is EXW and the rest of the sentence is just the rest of it.
SPELLED_OUT: tuple[tuple[tuple[str, ...], str], ...] = (
    (("EX", "WORKS"), "EXW"),
    (("EXWORKS",), "EXW"),
    (("FREE", "ON", "BOARD"), "FOB"),
    (("COST", "AND", "FREIGHT"), "CFR"),
    (("COST", "INSURANCE", "FREIGHT"), "CIF"),
    (("CARRIAGE", "PAID", "TO"), "CPT"),
    (("DELIVERED", "DUTY", "PAID"), "DDP"),
    (("DELIVERED", "AT", "PLACE"), "DAP"),
    (("FREE", "CARRIER"), "FCA"),
)


def canonical_basis(text: str) -> str:
    """The delivery term a row may carry: an Incoterm, a known price basis, or Unspecified.

    A reader handed prose puts prose in the field - "delivery: Linyi; formal Incoterm n/s" arrived
    on the Price Monitor of 20 October 2026. Since `commercial_basis` is part of an identity's
    comparability group, that wording would have opened a market of its own, holding one price,
    joined to nothing.

    An Incoterm named anywhere in the wording is taken, because "FOB China export" is a quote on
    FOB terms. Wording that names none becomes Unspecified, which is the honest reading of a
    publisher who did not state terms. The original wording survives in the row's quote either way.
    """
    raw = str(text or "").strip()
    if not raw:
        return UNSPECIFIED
    upper = raw.upper()
    if upper in UNSTATED:
        return UNSPECIFIED
    if upper in INCOTERMS or upper in BASIS_NAMES:
        return upper
    # An Incoterm inside a longer phrase, standing on its own: "FOB China export".
    # Split on anything that is not a letter or a digit rather than matched with a pattern, so
    # a term is only taken where it is a word of its own - the "CIF" inside "PACIFIC" is not a
    # delivery term.
    words = set("".join(character if character.isalnum() else " "
                        for character in upper).split())
    found = sorted(INCOTERMS & words)
    if not found:
        # Written out rather than abbreviated: "cash ex works", "free on board".
        found = sorted({code for needed, code in SPELLED_OUT if set(needed) <= words})
    if len(found) == 1:
        return found[0]
    # Two of them, or none: the field cannot say which, so it says nothing and the quote keeps the
    # wording.
    return UNSPECIFIED

# A basis that names neither end of the journey. Defined here, above the two readers that need it:
# `names_delivery_terms` just below, and the side rule further down.
#
# REGIONAL_MARKET is the whole of it, and keeping it out of the two side sets is **ruled
# 28 October 2026** - the one ruling masterplan C was waiting on.
#
# The engine kept a second copy of the side rule with REGIONAL_MARKET on the DESTINATION side,
# arguing a regional assessment is a price *in* a market, and `derive` wrote
# `destination = <the region>` on all 31 of the store's regional assessments. That reads in the
# store exactly like a CIF import price, and it is a claim no publisher made: IMARC and
# BusinessAnalytiq print a price level for a region, with no consignment and no journey. The proof
# that it misleads is `attribution_note`, the engine's own prose for these columns, which turned
# `destination = Europe` into "Delivered into Europe" - false of a regional index, and deleted with
# the second copy. Nothing is lost by saying nothing: the region is in `market_geography`, where
# every reader already looks. Migration 0061 cleared the 31.
NAMES_NEITHER_END: frozenset[str] = frozenset({"REGIONAL_MARKET", "REGIONAL MARKET"})

# A regional assessment is a number for a region, on no stated delivery terms. It belongs in the
# selector - the selector is headed "Incoterm/Price basis" and it is a price basis - but it is not
# a market a page should open on while that market holds a real assessment. TDI North America was
# landing on four monthly index points because they were the most recent thing there, in front of
# 492 weeks of the delivered bulk series (Stefan, 27 September 2026).
NAMES_NO_DELIVERY_TERMS = UNSTATED | NAMES_NEITHER_END


def names_delivery_terms(basis: str) -> bool:
    """Whether this basis says where a price is delivered or collected."""
    return str(basis or "").strip().upper() not in NAMES_NO_DELIVERY_TERMS

# Two labels, for two populations.
#
# A customer is offered only markets priced on an Incoterm (Stefan, 20 September 2026), so on every
# customer surface - both market selectors, the chart download, the delivered file - the dimension
# holds Incoterms and nothing else, and is called what it is.
#
# The internal screens list every series the store holds, and there the same field carries a
# producer's list price, a seller's quote, a regional assessment and a transaction record beside
# the Incoterms. "Incoterm" is untrue of more than half of those rows, so the internal tables keep
# the wider word.
# Stefan, 23 September 2026: "Incoterm/Price basis". The list holds delivery terms and it holds the
# kind of price - a published assessment on DDP, a negotiated price on DAP, a regional assessment
# with no delivery term at all - and the label now names both rather than one of them.
INCOTERM_LABEL = "Incoterm/Price basis"
BASIS_LABEL = "Price basis"


def display_basis(value: object) -> str:
    """The basis as a reader should see it. An unknown code is returned readable rather than raw."""
    code = str(value or "").strip()
    if code.upper() in UNSTATED:
        return "Unspecified"
    if code.upper() in BASIS_NAMES:
        return BASIS_NAMES[code.upper()]
    # An Incoterm, or a code nobody has named yet. Both are better shown than swallowed; a code that
    # arrives from a new publisher reads as words instead of as SHOUTING_WITH_UNDERSCORES.
    if code.isupper() and "_" in code:
        return code.replace("_", " ").capitalize()
    return code


# Which end of the journey a basis quotes the price at - and so which of a market identity's two
# geography fields carries the meaning.
#
# `market_geography` is side-neutral. The store reads the basis and fills `origin` OR `destination`
# from it; a page that wants to NAME that geography has to ask the same question or it will answer
# with the wrong one. The Forecast card did not ask: it took the Geography selector into a variable
# called `origin` and printed "{geography} origin" under every basis, so "Polyether Polyol - Europe
# origin" sat over a CIF series whose rows read origin null, destination Europe - imports landing in
# Europe, labelled as exports leaving it.
#
# Stefan, 26 October 2026, holding the store's rows against the card: "I think there is something
# wrong in the data interpretation."
#
# ONE PAIR OF SETS, and this module is where they live (masterplan C, v1.2.10). Until then there
# were two: these, and a second pair in `market_data_engine/app/domain/origin.py` that disagreed on
# seven terms. The engine's `db/repositories.normalise_route` imported from here and was *dead
# code*; the live writer, `persistence/identity.py`, used the engine's own copy. So the test that
# said "one rule, not two copies" was checking the copy nobody ran. `domain/origin.py` imports from
# here now and keeps no set of its own.
#
# The union of the two, plus DAT, which neither had and which is a delivery term (Delivered At
# Terminal, the 2010 name for DPU). Every spelling a publisher actually writes is here rather than
# relying on `canonical_basis` having run first, because the engine's store writes the basis a
# connector hands it.
NAMES_A_DESTINATION: frozenset[str] = frozenset({
    "CFR", "CIF", "CIP", "CPT", "C&F", "DAP", "DAT", "DDP", "DDU", "DEL", "DELIVERED", "DPU",
    "FD"})
NAMES_AN_ORIGIN: frozenset[str] = frozenset({
    "EXW", "EX WORKS", "EX-WORKS", "EXWORKS", "FAS", "FCA", "FOB"})

# The three answers, named, so neither side of the application compares against a bare string. The
# engine used to answer the same question in capitals, which is a second spelling of one answer.
ORIGIN = "origin"
DESTINATION = "destination"
NEITHER = ""

def side_of(basis: str) -> str:
    """Which end of the journey this basis quotes at: ORIGIN, DESTINATION, or NEITHER.

    The one answer, for the card that names a geography and for the store that fills a route field.
    """
    upper = str(basis or "").strip().upper()
    if upper in NAMES_A_DESTINATION:
        return DESTINATION
    if upper in NAMES_AN_ORIGIN:
        return ORIGIN
    return NEITHER


def geography_phrase(geography: str, basis: str) -> str:
    """How a card should name a market's geography, given what its basis quotes.

    FOB Europe is material leaving Europe. CIF Europe is material landing in it. A regional
    assessment names neither end, so it gets the place and no claim about direction.
    """
    place = str(geography or "").strip()
    if not place:
        return ""
    side = side_of(basis)
    if side == ORIGIN:
        return f"out of {place}"
    if side == DESTINATION:
        return f"into {place}"
    return place


# A packaging the publisher did not state. Every spelling means the same silence, and a key that
# told them apart would mint a market per spelling. Kept here, with the other fields of the key,
# because four modules held their own copy of this set and a fifth was about to.
#
# Seven spellings, not the four the application used to carry: the engine's store folds all seven
# on write (`persistence/identity.py`, which now reads this set rather than keeping its own), so
# the three the application was missing - UNSPECIFIED, NOT_SPECIFIED and N/A - could never have
# reached it as themselves. Measured 27 October 2026: the store holds exactly three packaging
# values, "", "bulk" and "drums", so widening the set changes no count today. It closes the gap
# before an import writes one of the three straight past the fold.
NOT_STATED: frozenset[str] = frozenset(
    {"", "UNKNOWN", "UNSPECIFIED", "NONE", "NOT SPECIFIED", "NOT_SPECIFIED", "N/A"})


def stated_packaging(value: Any) -> str:
    """The packaging as the key reads it: what was stated, or "" for every way of saying nothing."""
    text = str(value or "").strip()
    return "" if text.upper() in NOT_STATED else text
