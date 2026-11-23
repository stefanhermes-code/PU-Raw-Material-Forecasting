"""Where the material was made, and where the price was quoted - which are not the same thing.

The Incoterm is what separates them, and nothing else does.

An origin-side term (EXW, FOB, FCA) is quoted at the seller's end, so its geography IS the origin:
"China FOB" is a price for Chinese-made material. A destination-side term (CFR, CIF, DAP, DDP) is
quoted at the buyer's end, so its geography is the DESTINATION and says nothing at all about who made
the material. A basis that is not a delivery term at all - a regional assessment, a producer's list
price, a seller's quote - names neither end, and this module fills neither field for it.

Which term is on which side is decided in one place, `raw_material_forecasting.market_basis`, and
imported here.

This matters commercially, not just pedantically. ECHEMI publishes seven TDI series and we assumed
the five export ones were Chinese exports, because ECHEMI is a Chinese platform. Checking the page
showed the column is headed "Area", there is no origin field, and where ECHEMI does know an origin it
puts it in the product name ("China Base oil - CFR"). The TDI export rows carry no such prefix. So a
customer entitled to Chinese-origin TDI can honestly be served China EXW and China FOB, and nothing
else - the rest has to be computed by the delivery module, not collected.

A publisher that states an origin is always believed over this rule; it only fills the silence.

A publisher that names a region inside a country is naming the same country. SunSirs prints its China
TDI ex-works assessment as "China" on most days and "East China" on a few; read literally that is two
markets and it split the publisher's own series into 37 days and 2. East China is where China's TDI is
made. `NATIONAL_FOR` folds the region back to the country - a national label is never widened into a
region, only the reverse.
"""
from __future__ import annotations

# The side rule lives in `raw_material_forecasting.market_basis` and this module keeps no copy of
# it (masterplan C, v1.2.10). It used to keep its own pair of sets, and the two disagreed on seven
# terms - two of them live in the store. The one that mattered was REGIONAL_MARKET: this file had it
# on the destination side and `derive` wrote `destination = <the region>` on all 31 of the store's
# regional assessments, which reads exactly like a CIF import price and is a claim no publisher
# made. Ruled 28 October 2026: a regional assessment names neither end, the region stays in
# `market_geography`, and migration 0061 clears the 31. The reasoning is written where the sets are.
from raw_material_forecasting.market_basis import (
    DESTINATION, NAMES_A_DESTINATION, NAMES_AN_ORIGIN, NEITHER, ORIGIN, side_of)

UNKNOWN = "UNKNOWN"

# Kept as names for anything that imported them from here. They are the same objects, not copies.
ORIGIN_SIDE_TERMS = NAMES_AN_ORIGIN
DESTINATION_SIDE_TERMS = NAMES_A_DESTINATION
# Re-exported with them, so a caller in the engine compares against a name rather than a literal.
SIDES = (ORIGIN, DESTINATION, NEITHER)


# Publishers that assess a country's regions as markets of their own, so their regional labels are
# NOT folded to the country.
#
# **The fold had no exception and needed one** (masterplan F). It was written for SunSirs, which
# prints its China TDI ex-works assessment as "China" on most days and "East China" on a few: one
# market under two names, which consolidates against itself. ICIS is not that. It assesses East,
# North and South China separately, with different values and different date ranges, and folding
# them averages three assessments into one series - the thing `reference/series.py` exists to
# prevent.
#
# The test is the publisher's own behaviour, and it is measurable: **a publisher that prices more
# than one region of a country means the regions.** Measured 28 October 2026 over the store's
# sub-national labels: `icis_history` holds three Chinese regions and 787 readings;
# `longzhong_polyester` holds one region and 3 readings, which is the SunSirs shape and folds.
#
# The application's own pages have ruled the same way from the other side - `market_selection_ui`
# keeps East, North and South China out of `ORIGINS` and draws them as lines inside China - so the
# page expected the store to keep the region while the store was folding it away. Migration 0064
# folded the three that should have been.
ASSESSES_REGIONS_SEPARATELY: frozenset[str] = frozenset({"icis_history"})

# Regional labels a publisher uses for a market that is national. Folding these keeps one series per
# market; without it the same assessment arrives under two names and consolidates against itself.
NATIONAL_FOR: dict[str, str] = {
    "east china": "China",
    "north china": "China",
    "south china": "China",
    "central china": "China",
    "northwest china": "China",
    "northeast china": "China",
    "southwest china": "China",
}


def _clean(value: str | None) -> str:
    return str(value or "").strip()


# Countries whose sub-national labels a publisher uses freely - a province, a city, a compass
# abbreviation, sometimes the seller's name - for a market that is national. A label naming one of
# these, and nothing else that is itself a market, is that country's market.
SUB_NATIONAL: tuple[str, ...] = ("China",)


def _words(value: str) -> set[str]:
    """The words of a label, split on anything that is not a letter or a digit.

    Split rather than matched with a pattern so a country is only found where it stands as a word
    of its own, and so no escape has to survive being written into this file.
    """
    return set("".join(character if character.isalnum() else " "
                       for character in value).upper().split())


def national(value: str | None, source_id: str | None = None) -> str:
    """The country a regional label belongs to, or the value unchanged.

    Exact regional labels fold through `NATIONAL_FOR`. Anything else naming exactly one country
    from `SUB_NATIONAL` folds to that country, which is what catches the shapes a reader lifts out
    of a document: "Shandong, China", "Shandong / Linyi, China", "NE China",
    "China; seller Nanjing Waien". Each of those opened a market of its own before 22 October 2026.

    A label naming none of them, or naming two, is returned unchanged: this widens a fold that
    already existed, and it is not a list of permitted geographies. A market this application has
    never seen must still be able to arrive.

    **`source_id` is the exception, and it is the whole of masterplan F's second item.** A publisher
    in `ASSESSES_REGIONS_SEPARATELY` means the region it names, so nothing is folded for it. Without
    this, ICIS's 787 readings of East, North and South China sat in markets no new reading could
    ever join - the writer folded the label away while the page drew the regions inside China.
    """
    if _clean(source_id) in ASSESSES_REGIONS_SEPARATELY:
        return _clean(value)
    cleaned = _clean(value)
    folded = NATIONAL_FOR.get(cleaned.lower())
    if folded:
        return folded
    words = _words(cleaned)
    named = [country for country in SUB_NATIONAL if country.upper() in words]
    if len(named) == 1 and cleaned.upper() != named[0].upper():
        return named[0]
    return cleaned


def derive(market_geography: str | None, commercial_basis: str | None,
           *, origin: str | None = None, destination: str | None = None,
           source_id: str | None = None) -> tuple[str | None, str | None]:
    """(origin, destination) for one identity, filling only what the publisher left silent.

    Anything the publisher actually stated is passed through untouched - this rule exists to stop us
    inventing an origin, not to overwrite one we were given.

    `source_id` reaches the regional fold: a publisher that assesses a country's regions separately
    keeps its region (masterplan F).
    """
    stated_origin, stated_destination = national(origin, source_id), national(destination, source_id)
    geography = national(market_geography, source_id)
    side = side_of(commercial_basis)

    if side == ORIGIN:
        return (stated_origin or geography or None, stated_destination or None)
    if side == DESTINATION:
        # The geography is where it lands. The origin is genuinely unknown unless someone said it,
        # and "not stated" is written one way only: NULL. It used to be written as the literal word
        # UNKNOWN here while every other path left the column empty, so one market was stored as two
        # identities - and because origin is not part of a line's key, both copies landed in the same
        # bucket and the duplicated publisher counted twice in the day's average. Twelve markets were
        # held that way. Stefan, 30 September 2026: de-duplicate it. Migration 0046 folded the pairs
        # together; this is the line that made them.
        return (stated_origin or None, stated_destination or geography or None)
    # No usable Incoterm: say nothing rather than guess a direction.
    return (stated_origin or None, stated_destination or None)


def origin_is_known(origin: str | None) -> bool:
    value = _clean(origin).upper()
    return bool(value) and value not in {UNKNOWN, "UNSPECIFIED", "NOT_SPECIFIED", "NOT SPECIFIED"}


# `attribution_note` was here: one sentence for a screen - "Quoted at origin: China-manufactured
# material." / "Delivered into Europe; the publisher does not state where the material was made."
# It had no caller anywhere outside its own test, so it was a third statement of the side rule in
# prose, free to drift from the two that ran. Deleted 28 October 2026 rather than wired up, because
# the surface it would have gone on is the one C fixed by taking a claim OFF it: the Current Market
# page shows Geography and Incoterm as two labelled fields and claims no direction, which is right.
# Writing the sentence again is one line from `side_of` and `origin_is_known` if it is ever wanted,
# and `market_basis.geography_phrase` is the shorter form already on the forecast banner.

