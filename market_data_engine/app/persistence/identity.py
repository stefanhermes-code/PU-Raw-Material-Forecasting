"""The one place a market identity is found or created.

There used to be two: `PersistentMarketDataService._ensure_identity` for live collection and a private
copy inside `app/bootstrap.py`. They agreed until v0.4.70 taught the live one to derive `origin` from
the Incoterm. The bootstrap copy went on searching for `origin = None`, matched nothing, and minted a
duplicate identity for every governed series - splitting each series' history from its new rows and
dropping the TDI China EXW reference onto a single publisher.

The defect was never the origin rule. It was that the same decision lived in two places and only one
of them was taught. So both callers now come here.

What each caller still owns is how it *builds* a `MarketIdentity` - the live path resolves one from a
connector record (parsing the product name, normalising packaging), the bootstrap file maps one from
already-governed fields. Those are genuinely different jobs. Matching and writing the row is not.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketIdentityModel
from app.domain.origin import derive as derive_origin, national
from app.domain.schemas import MarketIdentity
from app.domain.series_key import FIELDS

# Every field that makes two observations the same series: the eight the series key is named by,
# plus origin and destination. Taken from `FIELDS` rather than typed out, so what mints a series
# and what names it are one list - they were two, and nothing but care kept them in step.
#
# Origin and destination are the two extras: a China-origin FOB price and an unattributed CFR
# price are not the same market. They are also why migration 0046 had duplicates to repair, which
# section C of the masterplan records; keeping them is a standing ruling, not an accident.
MATCH_FIELDS: tuple[str, ...] = FIELDS[:2] + ("origin", "destination") + FIELDS[2:]
# Nine fields since migration 0059: currency came out of `FIELDS` with it (masterplan A, item 5).
# The currency a reading is priced in is on the READING now; the column of the same name on the
# identity is the currency of record for the readings it holds, and identifies nothing.


# A publisher who states no packaging. "UNKNOWN" and "" say the same thing as nothing at all, and
# saying it seven ways makes seven markets. One copy, in `market_basis`, with the other fields of
# the market key - this module held the longest of four copies and the customer pages held a
# shorter one, so a spelling folded here was a market there.
from raw_material_forecasting.market_basis import NOT_STATED

# Every spelling of a metric tonne is the same unit, and two spellings are two markets. Five
# modules held their own list and no two matched; one list, in `units`, which also decides the
# token. The token is MT from migration 0057 (masterplan A, item 4), not "t".
from raw_material_forecasting import chemical_vocabulary, identity_vocabulary, price_kinds
from raw_material_forecasting.chemicals import canonical_code
from raw_material_forecasting.units import canonical_unit


class Refused(Exception):
    """This reading does not become a market (masterplan D, v1.2.11).

    Raised rather than returned, because a caller that forgets to check would write the market
    anyway. The door - live collection and the document imports - catches it and writes the reading
    to `quarantined_reading`.

    `refusals` is every gate the identity failed, not the first: a document read wrongly usually
    fails more than one.
    """

    def __init__(self, refusals):
        self.refusals = tuple(refusals)
        super().__init__("; ".join(one.said() for one in self.refusals))


def canonical_packaging(value: str | None) -> str | None:
    """Packaging, or nothing where the publisher stated nothing."""
    text = str(value or "").strip()
    return None if text.upper() in NOT_STATED else text


# The gate is every caller's, with no exemption and no flag to turn it off.
#
# It was briefly exempted for three callers - `bootstrap` and the two history loaders - on the
# reasoning that they read HTC's own governed records rather than a publisher's string. The
# reasoning was sound and the shape was wrong: an exemption is a hole, and the hole was opened
# because of five rows of `data/research/purchase_orders.csv` whose chemical column says `MDI`.
# Stefan ruled on those rows the same day (MDI means pMDI, migration 0063), so the hole was not
# needed - and a hole kept "in case" is the thing this whole plan is about.
#
# What the episode did settle is that a refusal on one of those paths must not throw a row away.
# They catch `Refused`, count the row rejected with the reason in their notes, and write it to the
# quarantine like any other - so an unknown family in HTC's own records is loud and visible rather
# than either silently filed or silently dropped.
def ensure_identity(session: Session, identity: MarketIdentity,
                    source_id: str = "", *,
                    cache: dict | None = None) -> MarketIdentityModel:
    """Find the row for this identity, or create it. Origin is derived before the lookup.

    Deriving *before* matching is the whole point: search on the value that will be stored, or the
    next run misses the row it wrote last time. The same applies to folding a regional label back to
    its country - do it after the lookup and the row is found by one spelling and written with
    another.

    Raises `Refused` where a gated field holds something the application cannot read (masterplan D).
    Three fields are gated and four are deliberately not, and `identity_vocabulary` is where that
    is argued. Every caller handles it: the live collection and the two history loaders write the
    row to the quarantine and carry on; `bootstrap` lets it propagate, because a refusal there means
    the bootstrap file disagrees with the vocabulary and that is a fault to see.

    `source_id` is needed for one value only - a customs `market_price` is a deal, not an
    assessment.

    `cache` is the caller's own dictionary, keyed by the derived fields below, and it exists because
    a statistical source asks this question once per record about the same handful of markets:
    measured 19 November 2026, 1,059 calls and 37.4 seconds for one UN Comtrade window over 142
    markets. It is the caller's and not this module's because its life is one collection run - a
    cache kept here would answer next month's run with this month's row. The gate above it still
    runs on every record, cache or no cache, because a refused reading must still be quarantined.
    """
    # Before anything is derived or looked up, because a refused reading must leave no trace: the
    # lookup itself is harmless, but deriving an origin for a market that will not exist is work
    # done on a value nobody has agreed to keep.
    stopped = identity_vocabulary.refusals({
        identity_vocabulary.FAMILY: identity.product_family,
        identity_vocabulary.UNIT: identity.unit,
        identity_vocabulary.PRICE_KIND: identity.observation_type,
    }, source_id=source_id)
    if stopped:
        raise Refused(stopped)
    origin, destination = derive_origin(
        identity.market_geography,
        identity.incoterm_or_commercial_basis,
        origin=identity.origin,
        destination=identity.destination,
        # The publisher decides whether its regional label is a region or a spelling of the country
        # (masterplan F): ICIS assesses three Chinese regions, SunSirs prints one market two ways.
        source_id=source_id,
    )
    fields = {
        # Upper-cased and folded forward through the renamed codes, which is what every reader
        # compares under: POLYOL became POLYETHER_POLYOL at migration 0035 and MDI became PMDI at
        # 0063. The gate above has already agreed it is a family the application prices.
        "product_family": canonical_code(identity.product_family),
        # Folded for the same reason the unit, the packaging and the price kind are: the grade
        # field holds a grade CODE or nothing (masterplan A, items 1-3). A supplier's product name
        # resolves upward to the grade it is an example of, commentary resolves to nothing, and a
        # spelling the vocabulary does not know resolves to nothing rather than minting a market
        # out of whatever a publisher typed. Which product a reading is of goes on the reading.
        "product_grade_or_chemistry": chemical_vocabulary.grade_code_for_the_store(
            identity.product_grade_or_chemistry, identity.product_family) or None,
        "origin": origin,
        "destination": destination,
        # Folded for the same reason origin is: SunSirs names the region in both fields, and
        # "East China" in either one mints a second row for a market that already has one.
        "market_geography": national(identity.market_geography, source_id),
        "commercial_basis": identity.incoterm_or_commercial_basis,
        # Folded for the same reason the geography above is: one market, written two ways, is two
        # rows and two halves of one series.
        "packaging": canonical_packaging(identity.packaging),
        "unit": canonical_unit(identity.unit),
        # Folded for the same reason the unit is: the store keeps four kinds of price and used to
        # keep seven, so a caller still writing `market_price` would mint a second row beside the
        # `assessed` one it belongs to. `app/bootstrap.py` builds an identity from already-governed
        # fields and does not fold anything itself, which is exactly why the fold belongs here -
        # the whole point of this module.
        # The fold, not the gate: `kind_of` is the forgiving version, which folds the seven old
        # spellings to the four kinds. The gate above has already refused anything it cannot read,
        # so what reaches here is one of the four or one of the seven.
        "observation_type": price_kinds.kind_of(identity.observation_type, source_id),
    }

    # The fields are what the lookup matches on, so they are what the cache is keyed by. Derived
    # first and cached after, so a reading whose origin folds to the same market as another hits the
    # same entry.
    key = tuple(sorted(fields.items()))
    if cache is not None:
        known = cache.get(key)
        if known is not None:
            return known

    stmt = select(MarketIdentityModel)
    for column, value in fields.items():
        stmt = stmt.where(getattr(MarketIdentityModel, column) == value)
    row = session.scalar(stmt)
    if row is not None:
        if cache is not None:
            cache[key] = row
        return row

    # The currency is written but not matched on (migration 0059, masterplan A, item 5): it is the
    # currency of record for the readings this series holds, and what a reading is priced in is on
    # the reading. A series that comes to hold two currencies has it emptied, and the answer is
    # then the reading's - which is why it is not in `fields` and so not in the WHERE above.
    row = MarketIdentityModel(**fields, currency=str(identity.currency or "").strip().upper(),
                              comparability_group=identity.comparability_group)
    session.add(row)
    # Flushed, unlike a price or a feature row: everything written afterwards names this market by
    # its id, so the id has to exist before the next record is read.
    session.flush()
    if cache is not None:
        cache[key] = row
    return row
