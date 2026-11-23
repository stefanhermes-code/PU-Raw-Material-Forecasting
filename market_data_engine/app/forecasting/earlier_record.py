"""The record this market had before the one we are collecting now.

Stefan, 3 November 2026, on Aliphatic Polyester Polyol China EXW: *"Of course it can be used as
history for forecasting and used for backtracking/testing various forecast methods on."*

He had ruled this once already, on 13 October 2026, and `eligibility.py` quotes him: *"do not
exclude these from the evaluation ... these past decisions must be evaluated continuously ... Each
individual series may be not eligible but glued together they could very well be."* That ruling was
applied to the customer catalogue and never to the history a forecast is fitted on - one rule, two
places, and the copy that ran was the one nobody had ruled on, which is the shape this application
keeps finding.

WHAT THIS GLUES, AND WHAT IT REFUSES TO
----------------------------------------
A market can be held as several stored series: different grades, and a transaction record beside a
publisher's assessment. Where one of them **ends before another begins**, the older one is this
market's earlier record. It is not a rival series; it is the same market, written down by somebody
else, before we started collecting it ourselves.

Measured against the live store on 3 November 2026, of the 59 lanes the Forecast page offers,
exactly **two** have that shape:

    ALIPHATIC_POLYESTER_POLYOL | China | EXW
        the page forecasts    9 readings   2026-09-28 .. 2026-10-30   (2 months)
        behind it            41 readings   2014-01-01 .. 2017-05-01   deal, low-fogging grade
        behind it            41 readings   2014-01-01 .. 2017-05-01   deal, grade unstated
        the hole: 3,437 days

    POLYETHER_POLYOL | China | FOB
        the page forecasts    2 readings   2026-08-01 .. 2026-08-01   (1 month)
        behind it           104 readings   2000-01-01 .. 2025-01-01   deal, grade unstated
        the hole: 577 days

Both showed the reader no forecast at all, because two months of our own collection cannot clear
the history bar - while 41 and 104 months of the same market sat in the store unread.

A series that merely overlaps is NOT glued. Two live records of one market are a question about
which is the market, and v1.2.57 to v1.2.59 answered that; this is the different question of what
came before.

THE JOIN CARRIES THE SHAPE AND NOT THE LEVEL, AND THE REASON IS STEFAN OWN
---------------------------------------------------------------------------
`europe_chain`, 22 September 2026: *"Joined as levels, the join itself would read as a market
move ... and the method would carry it forward as a trend."* The same applies here and harder,
because the records are nine years apart. Aliphatic Polyester's deal record ends at CNY 16,928 in
May 2017 and our assessment opens at about CNY 12,800 in September 2026. Concatenated raw, a method
reads a nine-year slide that nobody measured and projects it onward.

So the earlier record is **scaled onto the live series' first price**: every month is multiplied by
one number, the record's own shape is untouched, and the join is exactly flat. What crosses the hole
is the market's shape - its volatility, its seasonality, how far it moves in a month - which is what
a method is fitted on and what a backtest tests. What does not cross is a price level nobody can
vouch for after nine years.

**Proportional and not additive, measured rather than assumed.** `europe_chain` shifts by a
constant sum of money, and its reason - the method carries its error in money, so a constant shift
moves the answer by exactly that amount - held because its two levels are close: the card runs at
67-79% of the published series. Here they are not. Measured on 3 November 2026, the mean monthly
swing of each record, as its own record and after each kind of shift:

    record                                   ratio   its own   additive   proportional
    Aliphatic Polyester, low-fogging          0.74     2.05%      2.87%          2.05%
    Aliphatic Polyester, grade unstated       0.82     2.51%      3.14%          2.51%
    Polyether Polyol China FOB                2.88     4.61%      2.01%          4.61%

A constant shift moves a price level without moving the swings that go with it, so it inflates the
record's volatility when it shifts down and flattens it when it shifts up - on Polyether Polyol it
halved it. The shape is the only thing crossing the hole, so destroying it is the one failure this
join cannot afford. A proportional shift leaves it exactly as it was, which the third column shows
by construction. This is also the ordinary way a long price history is carried onto a new level.

**Where the lane holds more than one earlier record they are averaged, after each is shifted.**
Aliphatic Polyester has two, a low-fogging grade and an unstated one, both bought by the same
buyer in the same 41 months. They move together - 33 of 40 monthly moves in the same direction,
their ratio steady between 1.038 and 1.150 - so they are two grades of one market, and the mean of
their shapes is a better reading of that market than either alone. It also means no tie-break has
to choose between them, which is the accident this application spent v1.2.57 to v1.2.59 removing.

WHERE IT IS BUILT, AND WHY HERE
--------------------------------
In `store_series.forecast_rows`, the one door every forecast comes through: the page, the governed
production run, the review job and the overrides. Masterplan F paid for that lesson - the Europe
chain was built inside the `/forecast` handler, so it reached one method of eighteen, three markets
of 89 and no governed forecast at all. Glued here, every method is fitted on the same history and
the request carries no special case.

Only the months before the market's own earliest month are returned, so its own readings are
untouched: the latest print, the current price and the chart are exactly as they were. What changes
is how far back the history reaches.
"""
from __future__ import annotations

from datetime import date

# What a glued month is filed as. Not a publisher: these months are an earlier record of this market
# carried onto its current level, so they say so rather than borrowing a source id that would make
# them look like something we collected.
EARLIER_SOURCE = "earlier_record"

# An earlier record shorter than this is not worth gluing: it is the same bar the forecast itself
# has to clear (`HISTORY_MIN_POINTS["monthly"]`), so a record that could not carry a forecast on its
# own does not become history for another series by being old.
MIN_MONTHS = 6


def shifted_onto(earlier: dict[date, float], first_price: float) -> dict[date, float] | None:
    """`earlier`, scaled so its last month sits on `first_price` and every swing is unchanged.

    Every month is multiplied by one number, so each month-on-month move keeps its size **in per
    cent** - which is the record's shape, and the only thing this join carries across the hole.

    None when there is nothing to scale, or when a price at or below zero makes the scale
    meaningless: a record holding one is not a record of anything, and handing it to a model is
    worse than handing it nothing.
    """
    if not earlier or first_price <= 0:
        return None
    last = earlier[max(earlier)]
    if last <= 0 or any(value <= 0 for value in earlier.values()):
        return None
    scale = first_price / last
    return {month: value * scale for month, value in earlier.items()}


def averaged(records: list[dict[date, float]]) -> dict[date, float]:
    """The mean of several already-shifted records, month by month.

    A month held by one record and not another is that record's alone; nothing is interpolated, and
    a month nobody holds stays missing. The hole between the earlier record and the live series is
    left as a hole: it is real, the chart draws it as a bridge, and filling it would be inventing
    prices.
    """
    months: dict[date, list[float]] = {}
    for record in records:
        for month, value in record.items():
            months.setdefault(month, []).append(value)
    return {month: sum(values) / len(values) for month, values in sorted(months.items())}


def continued(session, identity, rows, *, rows_for):
    """The months this market was priced in before the series we hold started.

    `rows_for(identity_id)` fetches a sibling's rows; passed in rather than imported, so this module
    does not import the module that calls it - the same arrangement `europe_chain` uses.
    """
    from app.db.models import MarketIdentityModel, PriceObservationModel
    from app.forecasting.service import MONTHLY, bucket
    from sqlalchemy import func, select

    card = bucket(rows, MONTHLY)
    if not card:
        return []
    first_of_its_own = min(card)
    first_price = float(card[first_of_its_own])

    siblings = [
        other for other in session.scalars(
            select(MarketIdentityModel)
            .where(MarketIdentityModel.product_family == identity.product_family)
            .where(MarketIdentityModel.market_geography == identity.market_geography)
            .where(MarketIdentityModel.commercial_basis == identity.commercial_basis))
        if other.id != identity.id
        # The scale is a ratio between two prices, so the two records have to be in the same money.
        # A sibling kept in another currency or another unit is the same market in different terms,
        # and converting it here would put an FX decision inside a history join.
        and str(other.currency or "").upper() == str(getattr(identity, "currency", "") or "").upper()
        and str(other.unit or "").upper() == str(getattr(identity, "unit", "") or "").upper()
    ]
    if not siblings:
        return []

    # WHICH SIBLINGS ARE WORTH FETCHING, ASKED ONCE ON THE SESSION WE ALREADY HOLD
    #
    # `rows_for` opens a database session of its own. Called once per sibling, from inside a
    # session, on every market the forecast warm-up touches, it emptied a connection pool of
    # fifteen: 1,566 `EMAXCONNSESSION` failures in one afternoon, and the page - which asks the
    # engine with a 1.5-second timeout by design - got nothing and reported "Contact HTC Global to
    # add markets to your subscription" over a store holding 339 identities.
    #
    # What decides a sibling is where its record ENDS, and the store can answer that for all of
    # them in one query without reading a single price. Only a sibling that really does end before
    # our own record begins is then fetched - two markets in the whole application.
    spans = dict(session.execute(
        select(PriceObservationModel.identity_id, func.max(PriceObservationModel.observation_date))
        .where(PriceObservationModel.identity_id.in_([other.id for other in siblings]))
        .where(PriceObservationModel.price.is_not(None))
        .group_by(PriceObservationModel.identity_id)).all())

    shifted: list[dict[date, float]] = []
    for other in siblings:
        newest = spans.get(other.id)
        if newest is None:
            continue
        newest = newest.date() if hasattr(newest, "date") else newest
        # Ends before ours begins. A record that overlaps is a rival reading of the same months,
        # which is the question `forecast_identity_for` answers, not this one.
        if date(newest.year, newest.month, 1) >= first_of_its_own:
            continue
        earlier = bucket(rows_for(other.id), MONTHLY)
        if len(earlier) < MIN_MONTHS or max(earlier) >= first_of_its_own:
            continue
        moved = shifted_onto(earlier, first_price)   # scaled, so its swings survive the join
        if moved is not None:
            shifted.append(moved)

    if not shifted:
        return []
    return [
        {"source_id": EARLIER_SOURCE, "observation_date": month, "native_price": float(value),
         # The month itself. These are months somebody held a price in at the time, so a rolling
         # backtest reading this must not be told a 2014 month became knowable in 2026.
         "available_from": month}
        for month, value in sorted(averaged(shifted).items())
        if month < first_of_its_own
    ]
