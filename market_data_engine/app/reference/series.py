"""The consolidated series: one governed value per date, from every publisher that printed.

Four publishers quote China TDI ex-works and they do not agree. On the ten days more than one of
them printed, the spread between the highest and the lowest ran 0.2% to 1.6%, median 0.64%. None of
them is the market; the market is what they are all measuring.

So the engine stops carrying five series and carries one. The raw prints stay exactly where they are
- they are the evidence, and a consolidated value whose inputs are gone cannot be argued with - and
the consolidated series is written beside them, in `market_reference_point`, one point per date.
Decided by Stefan on 14 September 2026: from here the consolidated series is the series.

THE RULE, WHICH IS NOT NEW
--------------------------
`app/reference/daily.py` already held it: one latest value per source from a single date, arithmetic
mean, dates never blended. What was missing is that it only ever ran on the newest date, so the
engine had a consolidated *price* and no consolidated *history*. This applies the same rule to every
date the identity carries. One rule, not two - the number on the screen today and the number the
backtest scores against 2024 have to be produced the same way, or the backtest is measuring the
difference between two formulas.

Arithmetic mean rather than median, for the same reason: it is what the engine already used. Across
the ten overlapping days the two differ by at most 0.22%, which is not worth a second rule.

TAX COMES OUT HERE, NOT IN A LAYER OVER THE TOP
-----------------------------------------------
The publishers quote 含税 - inclusive of China's 13% VAT. The consolidated series is stored net of it,
so the stored value is the one that gets used and there is no second number anywhere that has to be
kept in step. The raw prints are left exactly as the publisher printed them: 16,700 is what SunSirs
said on 5 January 2024, and overwriting that would destroy the only thing that lets this be rechecked
if the convention turns out to be wrong.

Which prices carry the tax is decided by `app/domain/tax.py`, on who published them rather than on
what kind of record they are. That matters from the moment FoamPartner's Changzhou prices joined this
series: they are market prices now, and they never had VAT in them.

READINGS STEFAN HAS SET ASIDE DO NOT CONTRIBUTE
-----------------------------------------------
Going through TDI China on 26 September 2026 he made five cuts: a purchase record that ran on past
the point where a weekly assessment took over, an ERP posting that had been drawing a second line
under the first, four days that sat on top of a series already carrying them, an export list price
that did not move for a week, and a European series whose every value was the arithmetic mean of
another series already in the store.

Those cuts reached the Current Market chart and nothing else. The forecast, the backtest and this
series were still computed over all 180 of them, including 117 European months that were being
counted twice - which is precisely the thing a consolidated value is supposed to prevent. A ruling
about which evidence is sound belongs to the evidence, not to one chart (Stefan, 27 September 2026:
"of course I want all changed"). Nothing is deleted: the readings stay in the store, stay
exportable, and stop contributing to the number.

WHY A POINT IS REWRITTEN AND WHY IT IS VERSIONED
------------------------------------------------
A publisher can land late. A date that had two contributors yesterday can have four today, and its
consolidated value moves - not because the market moved, but because the evidence arrived. That is a
real revision and it is recorded as one: a new version of the same date, with the observation ids
that produced it. A point whose value and contributors are unchanged is left alone, so re-running
this costs nothing and writes nothing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    MarketIdentityModel, MarketReferencePointModel, PriceObservationModel, SourceRegistryModel,
)
from app.domain.tax import carries_chinese_vat, net_of_vat
from raw_material_forecasting import price_kinds
from app.reference.daily import _available_timestamp, observation_timestamp

# The rulings about which readings are sound. The dependency points this way because it is the only
# way round that works: the engine's own package is called `app`, and the application's entry point
# is a module called `app.py`, so the application cannot import the engine. One definition either
# way, which is the point - a reading out of the chart and inside the forecast is the defect this
# closes.
from raw_material_forecasting.series_curation import is_drawn


# Evidence ids for HTC's own records: signed orders and ledger postings. Everything else on this
# path was collected from a publisher.
OWN_RECORD_PREFIXES = ("po:", "history:")


def _is_own_record(row) -> bool:
    return str(getattr(row, "evidence_id", "") or "").startswith(OWN_RECORD_PREFIXES)


def contributors_by_date(rows) -> dict[date, list]:
    """One value per source per date, and how that value is chosen depends on the source.

    A publisher that prints twice in a day has revised itself, so its latest print stands and the
    earlier one is not a second vote.

    Two signed orders on one day are two purchases, and neither cancels the other - POM124030029 and
    POM124030030 were raised together at 72.000 and 78.000 baht, one for March delivery and one for
    April. Taking the later of those would throw a real price away, so the source's own records on a
    date are averaged instead.
    """
    grouped: dict[date, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        observed = observation_timestamp(row)
        if observed is None or getattr(row, "price", None) is None:
            continue
        source_id = str(getattr(row, "source_id", None) or "")
        if not source_id:
            continue
        grouped[observed.date()][source_id].append(row)

    out: dict[date, list] = {}
    for day, by_source in grouped.items():
        chosen = []
        for source_id in sorted(by_source):
            same_day = by_source[source_id]
            if len(same_day) == 1 or all(_is_own_record(row) for row in same_day):
                chosen.extend(same_day)
                continue
            chosen.append(max(same_day, key=lambda r: (_available_timestamp(r), str(r.id))))
        out[day] = chosen
    return out


def _rights_by_source(session) -> dict[str, str]:
    return {row.source_id: (row.rights_class or "")
            for row in session.scalars(select(SourceRegistryModel))}


def net_price(row, identity, rights: dict[str, str]) -> float:
    """One contributor's price on the basis the consolidated series is kept in: net of VAT."""
    price = float(row.price)
    if carries_chinese_vat(currency=identity.currency,
                           market_geography=identity.market_geography,
                           commercial_basis=identity.commercial_basis,
                           rights_class=rights.get(row.source_id),
                           source_id=row.source_id,
                           price_kind=identity.observation_type):
        return net_of_vat(price)
    return price


def consolidated_value(contributors, identity, rights: dict[str, str]) -> float:
    """One value per source, each net of tax, then the arithmetic mean. Dates never blended.

    Per source, not per row: a source that printed four times on a date must not outvote one that
    printed once, or whoever publishes most often quietly sets the price. Where a source has several
    rows on a date they are averaged into its one value first.
    """
    by_source: dict[str, list[float]] = defaultdict(list)
    for row in contributors:
        by_source[str(getattr(row, "source_id", "") or "")].append(net_price(row, identity, rights))
    if not by_source:
        raise ValueError("a consolidated value needs at least one contributor")
    per_source = [sum(prices) / len(prices) for prices in by_source.values()]
    return round(sum(per_source) / len(per_source), 2)


def contributes(identity: MarketIdentityModel, row: PriceObservationModel) -> bool:
    """Whether a reading counts towards the consolidated value.

    One definition of what is set aside, read here and by the chart, so a reading cannot be out of
    the customer's line and inside the number the forecast is built on.
    """
    return is_drawn(
        {"product_family": identity.product_family,
         "market_geography": identity.market_geography,
         "commercial_basis": identity.commercial_basis},
        row.source_id, row.observation_date)


# Only an assessed market is consolidated. The rule and the reason are in
# `rebuild_all_market_series` below; what matters here is that this is where it is enforced.
#
# Measured 29 October 2026: it was enforced in the caller instead, and this function has two
# callers. The bulk rebuild filtered; `persistence.service._update_reference`, which runs after
# every single collection, did not - so the store carried 18,907 consolidated points on 176
# `deal` identities and 149 on 4 `ask` identities, written as recently as the day before. A guard
# on one of two doors is not a guard. Nothing had gone wrong yet only because no deal or ask
# market has ever had two sources printing on one date; the first one would have averaged two
# sellers' quotes into a price nobody asked.
MAY_BE_CONSOLIDATED = price_kinds.ASSESSED


def may_be_consolidated(identity: MarketIdentityModel) -> bool:
    return str(identity.observation_type or "").strip().lower() == MAY_BE_CONSOLIDATED


def rebuild_reference_series(session: Session, identity_id: str, *, now=None) -> dict:
    """Write the consolidated series for one identity. Idempotent; returns what changed.

    A market whose kind may not be consolidated has every point it holds retired, so this is also
    how points written before the rule was enforced here are removed. The forecast falls back to
    the raw readings for such a market, net of tax - `store_series.forecast_rows` - which for one
    source on a date is the reading itself.
    """
    nothing = {"identity_id": identity_id, "dates": 0, "written": 0, "revised": 0}
    identity = session.get(MarketIdentityModel, identity_id)
    if identity is None:
        return nothing
    if not may_be_consolidated(identity):
        retired = 0
        for point in session.scalars(
                select(MarketReferencePointModel)
                .where(MarketReferencePointModel.identity_id == identity_id)):
            session.delete(point)
            retired += 1
        if retired:
            session.flush()
        return {**nothing, "retired": retired}
    rights = _rights_by_source(session)
    rows = [row for row in session.scalars(
        select(PriceObservationModel)
        .where(PriceObservationModel.identity_id == identity_id))
        if contributes(identity, row)]
    grouped = contributors_by_date(rows)

    # Every version of every date, and the latest of each. Both are needed: the latest is what a new
    # value is compared against, and all of them are what a retired date has to take with it - a
    # date is retired because nothing supports it any more, so leaving five older versions behind
    # would leave the thing that was withdrawn still standing in four of them.
    versions: dict[date, list[MarketReferencePointModel]] = {}
    for point in session.scalars(
            select(MarketReferencePointModel)
            .where(MarketReferencePointModel.identity_id == identity_id)
            .order_by(MarketReferencePointModel.version)):
        stamp = point.reference_date
        versions.setdefault(stamp.date() if isinstance(stamp, datetime) else stamp, []).append(point)
    existing = {day: points[-1] for day, points in versions.items()}

    # A date whose every contributor has been set aside stops being a date this market was priced
    # on. Without this the point stands and nothing revises it, because the rebuild only walks the
    # dates that still have contributors - so cutting a source that published alone changed nothing
    # at all, which is what the first run of this showed: 180 readings out, 0 values moved.
    #
    # These rows are derived. The observations they were computed from are untouched, still in the
    # store and still exportable, and running the rebuild again reproduces whatever the evidence
    # currently supports. That is why a retired point is removed rather than versioned: there is
    # nothing here to preserve that the evidence does not already hold.
    retired = 0
    for day, points in versions.items():
        if day not in grouped:
            for point in points:
                session.delete(point)
            retired += 1

    written = revised = 0
    for day, contributors in sorted(grouped.items()):
        value = consolidated_value(contributors, identity, rights)
        observation_ids = sorted(str(row.id) for row in contributors)
        current = existing.get(day)
        if current is not None:
            if (current.reference_value == value
                    and sorted(current.observation_ids or []) == observation_ids):
                continue
            version = current.version + 1
            revised += 1
        else:
            version = 1
        session.add(MarketReferencePointModel(
            identity_id=identity_id,
            reference_date=observation_timestamp(contributors[0]),
            reference_value=value, version=version,
            observation_ids=observation_ids,
            **({"calculated_at": now} if now is not None else {}),
        ))
        written += 1
    session.flush()
    return {"identity_id": identity_id, "dates": len(grouped),
            "written": written, "revised": revised, "retired": retired}


def rebuild_all_market_series(session_factory, *, product_family: str | None = None) -> list[dict]:
    """Rebuild the consolidated series for every published-price identity.

    Purchase records are not consolidated. They are one buyer's contracts, not a market anyone is
    measuring, and averaging BASF's 93 baht with Nagase's 64 in the same month would produce a price
    nobody paid.
    """
    with session_factory() as session:
        # The ASSESSED market and nothing else. "market_price" meant that here, and widening it to
        # every published kind would consolidate purchase records - averaging BASF's 93 baht with
        # Nagase's 64, which is what the docstring above forbids. The customs unit values that also
        # sat under the old literal are deals too, and a declared trade is not a market assessment.
        #
        # `rebuild_reference_series` enforces the same rule itself now, so this selection is about
        # not walking 180 identities to do nothing, not about correctness.
        query = select(MarketIdentityModel).where(
            MarketIdentityModel.observation_type == MAY_BE_CONSOLIDATED)
        if product_family:
            query = query.where(MarketIdentityModel.product_family == product_family)
        identity_ids = [row.id for row in session.scalars(query)]
        out = [rebuild_reference_series(session, identity_id) for identity_id in identity_ids]
        session.commit()
    return [row for row in out if row["dates"] or row.get("retired")]
