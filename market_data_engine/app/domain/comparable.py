"""Putting eight series on one footing.

Eight China TDI series sit in the store and none of them measure the same thing. Four are published
domestic assessments in CNY. One is FoamPartner's Changzhou purchase record in CNY. Two are KGI's
Thai purchases in THB. They differ in currency, in tax basis, and in Incoterm, and comparing them as
they stand compares those differences as much as the market.

This puts them on one footing: CNY per tonne, ex-VAT, at the rate of each observation's own date. It
does not touch the store. The stored price stays exactly as the publisher printed it or the buyer
paid it, because a converted number whose original is lost cannot be re-checked when the rate or the
rate rule turns out to be wrong. Every point that comes back out of here carries its original, its
currency, and the named steps that were applied to it.

WHAT IS STRIPPED, AND WHAT IS NOT
---------------------------------
**VAT, from published Chinese domestic assessments only.** The rule lives in `app/domain/tax.py` and
keys on who published the price, not on what kind of record it is - HTC's own books are net whatever
lane the row sits in. Note that the consolidated series in `market_reference_point` is already stored
net; this operates on the raw prints, which are kept exactly as the publisher printed them.

**FX, from prices struck in another currency.** The KGI purchases were agreed in baht for Chinese
material, so the baht number already contains the seller's own conversion. Restating it at the ECB
cross of the day the price was struck undoes that conversion at the moment it was made, rather than
at today's rate. This is only safe on prices that are real prints: run it over a figure that was
carried forward and it manufactures movement out of a number that never moved. That is why the
ledger clean-up came first.

**Incoterm is NOT stripped.** EXW, DAP, CIF and DDP are still four different places on the chain, and
the freight and duty between them are estimates, not evidence - `app/history/logistics.py` holds the
stack and says so in its own docstring. Deducting an estimate here would hide it inside a number that
looks measured. The Incoterm travels with every point instead, so whatever reads these knows what it
is holding.

WHAT COMES BACK WHEN A RATE IS MISSING
--------------------------------------
Nothing, for that point, and it is named in `skipped`. A price that could not be converted must not
come back looking converted - that is the failure this whole exercise exists to stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db.models import (
    FxRateModel, MarketIdentityModel, PriceObservationModel, SourceRegistryModel,
)
from app.domain.tax import CHINA_VAT_RATE, carries_chinese_vat
from app.fx.rates import RATE_LOOKBACK_DAYS, cross_rate, rate_table

BASE_CURRENCY = "CNY"



@dataclass(frozen=True)
class Step:
    """One named thing done to a price, and the factor it multiplied by."""
    name: str
    factor: float


@dataclass(frozen=True)
class ComparablePoint:
    observed: date
    price: float                 # CNY per tonne, ex-VAT
    original: float
    original_currency: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class ComparableSeries:
    label: str
    incoterm: str
    origin: str | None
    destination: str | None
    source_id: str
    observation_type: str
    points: tuple[ComparablePoint, ...]
    skipped: tuple[tuple[date | None, str], ...]




def comparable_series(session_factory, *, product_family: str = "TDI",
                      grade_contains: str | None = None) -> list[ComparableSeries]:
    """Every series for a product, on one footing, one entry per identity and publisher."""
    with session_factory() as session:
        table = rate_table(session)
        rights = {row.source_id: (row.rights_class or "")
                  for row in session.scalars(select(SourceRegistryModel))}
        rows = session.execute(
            select(MarketIdentityModel, PriceObservationModel)
            .join(PriceObservationModel,
                  PriceObservationModel.identity_id == MarketIdentityModel.id)
            .where(MarketIdentityModel.product_family == product_family)
        ).all()

    grouped: dict[tuple, list] = {}
    for identity, observation in rows:
        grade = identity.product_grade_or_chemistry or ""
        if grade_contains and grade_contains.lower() not in grade.lower():
            continue
        key = (grade, identity.origin, identity.destination, identity.market_geography,
               identity.commercial_basis, identity.currency, identity.observation_type,
               observation.source_id)
        grouped.setdefault(key, []).append(observation)

    out: list[ComparableSeries] = []
    for key, observations in grouped.items():
        grade, origin, destination, geography, basis, currency, kind, source_id = key
        points: list[ComparablePoint] = []
        skipped: list[tuple[date, str]] = []
        # An observation with no date cannot be placed on a timeline or converted at its own rate.
        dated = [o for o in observations if o.observation_date is not None]
        undated = len(observations) - len(dated)
        for observation in sorted(dated, key=lambda o: o.observation_date):
            when = observation.observation_date
            when = when.date() if isinstance(when, datetime) else when
            price = float(observation.price)
            steps: list[Step] = []

            if carries_chinese_vat(currency=currency, market_geography=geography,
                                   commercial_basis=basis, rights_class=rights.get(source_id),
                                   source_id=source_id, price_kind=kind):
                factor = 1.0 / (1.0 + CHINA_VAT_RATE)
                price *= factor
                steps.append(Step(f"less {CHINA_VAT_RATE:.0%} China VAT", factor))

            if currency.upper() != BASE_CURRENCY:
                found = cross_rate(table, when, currency, BASE_CURRENCY)
                if found is None:
                    skipped.append((when, f"no {currency}/{BASE_CURRENCY} rate within "
                                          f"{RATE_LOOKBACK_DAYS} days"))
                    continue
                rate, quoted_on = found
                price *= rate
                steps.append(Step(f"{currency}/{BASE_CURRENCY} at {quoted_on.isoformat()}", rate))

            points.append(ComparablePoint(
                observed=when, price=round(price, 2), original=float(observation.price),
                original_currency=currency, steps=tuple(steps)))

        if undated:
            skipped.append((None, f"{undated} observation(s) carry no date"))

        label = " / ".join(part for part in (grade or product_family, source_id) if part)
        out.append(ComparableSeries(
            label=label, incoterm=basis, origin=origin, destination=destination,
            source_id=source_id, observation_type=kind,
            points=tuple(points), skipped=tuple(skipped)))

    out.sort(key=lambda s: (-len(s.points), s.label))
    return out
