"""Every reading, kept in every currency the page can show it in.

Stefan, 27 September 2026: "when you import the value and then convert it to USD with the correct
FX rate is it not easier to just store it as USD value. So this conversion is done once and not
every time you load the graphs?"

Yes. The page used to fetch the prices in one call and the ECB rates in another. The rate call had
a six-second limit meant for "is the engine alive", it ran on the first render while the engine was
still warming its forecasts, and when it timed out the empty rate table it fell back to was kept
for the rest of the session. Every yuan reading then passed through unconverted onto a dollar axis,
and the Current Market card read USD 17,533 for a market that was 2,617 dollars. Nothing on the
page could tell.

So the arithmetic happens here, once, when the datapoint is created, and the page reads a stored
number. A page that does no arithmetic cannot do half of it.

WHY THIS IS A SWEEP RATHER THAN A HOOK

Six different places write a reading: the repository, the bootstrap, the purchase-order and
transaction loaders, the cloud persistence service, and two import scripts. A hook in each is a
hook somebody forgets, and the way you find out is a chart with a hole in it. This looks for
readings that have no value in a currency yet and fills them, so it does not matter who wrote them
or when.

WHAT IT REFUSES TO DO

A day the ECB did not quote takes the last quote before it, up to the lookback the rate table
allows; nothing is interpolated. Where no quote is within reach - the SE Asia weeks from 2010 to
2013, which predate the earliest fixing we hold - no row is written. The page then draws nothing
for that reading in that currency, which is the answer the 25th of September should have given.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceInCurrencyModel, PriceObservationModel
from app.fx.rates import cross_rate, rate_table

# The currencies a reader can choose. Kept here rather than imported from the application, because
# the engine must be able to fill the store without the application running.
#
# Stefan, 27 October 2026: "Drop THB. Only USD, EUR and CNY." A reading published in baht is
# still read and still converted - KGI's Thai purchases are paid in baht - but baht is not a
# currency the store keeps a column of, and not one a reader is offered.
CURRENCIES: tuple[str, ...] = ("USD", "EUR", "CNY")


def _day(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _reading_day(price: PriceObservationModel) -> date | None:
    """The day the price was struck. What decides which fixing applies."""
    for value in (price.observation_date, price.publication_date, price.available_from):
        day = _day(value)
        if day is not None:
            return day
    return None


def convert_readings(session, *, currencies: tuple[str, ...] = CURRENCIES,
                     redo_from: date | None = None) -> dict[str, int]:
    """Fill in every reading's value in every currency. Returns what it did.

    `redo_from` re-does readings dated on or after a day, for the stretch at the live end where a
    reading was taken before that day's fixing was published and converted at the previous one.
    """
    table = rate_table(session)
    wanted = tuple(c.upper() for c in currencies)

    held: dict[str, set[str]] = {}
    held_values: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for observation_id, currency, value, rate in session.execute(
            select(PriceInCurrencyModel.observation_id, PriceInCurrencyModel.currency,
                   PriceInCurrencyModel.price, PriceInCurrencyModel.fx_rate)):
        held.setdefault(observation_id, set()).add(currency)
        held_values[(observation_id, currency)] = (value, rate)

    rows = session.execute(
        select(PriceObservationModel, MarketIdentityModel.currency)
        .join(MarketIdentityModel, PriceObservationModel.identity_id == MarketIdentityModel.id)
    ).all()

    # The stretch at the live end is cleared first and written again below. Clearing it inside the
    # writing loop would delete the rows that loop had just written, one observation at a time.
    stale = [price.id for price, _ in rows
             if redo_from is not None and (_reading_day(price) or date.max) >= redo_from
             and price.id in held]

    # A reading whose price was revised after it was converted (Business Analytiq restates its
    # monthly figures, and the importer updates the row in place) still carries the conversion of
    # the old price. Stefan's adipic acid report of 14 October 2026 showed it: the stored dollar
    # value no longer matched price times rate. Any held value that disagrees with the reading's
    # own price at its own stored rate is converted again.
    stale_set = set(stale)
    for price, _assessed_in in rows:
        if price.id in stale_set or price.price is None:
            continue
        for currency in held.get(price.id, ()):
            value, rate = held_values.get((price.id, currency), (None, None))
            if rate and value is not None and abs(float(price.price) * float(rate) - float(value)) \
                    > 0.005 * max(1.0, abs(float(value))):
                stale.append(price.id)
                stale_set.add(price.id)
                break
    if stale:
        session.query(PriceInCurrencyModel).filter(
            PriceInCurrencyModel.observation_id.in_(stale)).delete(synchronize_session=False)
        for observation_id in stale:
            held.pop(observation_id, None)
    redone = set(stale)

    counted = {"written": 0, "redone": 0, "no rate": 0, "no date": 0, "readings": 0}
    for price, assessed_in in rows:
        counted["readings"] += 1
        day = _reading_day(price)
        if day is None:
            counted["no date"] += 1
            continue
        already = held.get(price.id, set())
        refresh = price.id in redone
        for currency in wanted:
            if currency in already:
                continue
            quote = cross_rate(table, day, assessed_in or "", currency)
            if quote is None:
                counted["no rate"] += 1
                continue
            rate, quoted_on = quote
            session.add(PriceInCurrencyModel(
                observation_id=price.id, currency=currency,
                price=float(price.price) * rate,
                price_low=None if price.price_low is None else float(price.price_low) * rate,
                price_high=None if price.price_high is None else float(price.price_high) * rate,
                fx_rate=rate, fx_rate_date=quoted_on))
            counted["redone" if refresh else "written"] += 1

    session.flush()
    return counted
