"""The history the Europe forecast reads: the published series, then the market's own changes.

Stefan, 22 September 2026. The published Western Europe DDP series runs from 2014 to July 2026 and
sits at a different price level from the market price on the card (TDI: the card at 67-79% of the
published price). Joined as levels, the join itself would read as a market move - about -1,250 USD
on TDI from July to August - and the method would carry it forward as a trend.

So the two are chained by CHANGES. The published series runs to its last month; after that, each
month moves by what the card moved from the month before. The whole line is then shifted by one
amount so that it ends on the card's price. The method uses fixed weights that add up to one and an
error in money, so shifting the line moves the forecast by exactly the same amount and changes
nothing else: the forecast is the card's price plus the change the method expects.

A month is chained when a European market for the same chemical holds it and the month before it.
The market's own months come first; a second European market for the same chemical stands in when
they are missing, because what is taken from it is the MOVE and not the level, and one chemical in
one region moves as one market (Stefan, 23 September 2026: TDI CFR had September alone and was
forecast across a two-month gap while the market beside it held August and September).

When the card's latest month is still past the chained line - pMDI has August alone and nothing
beside it - the method runs through the months between, and the answer is counted from the card's
latest month.
"""
from __future__ import annotations

from datetime import date

# Store code -> the published series' label in the reference store.
PUBLISHED = {
    "TDI": "TDI WE DDP",
    "PMDI": "MDI WE DDP",
    "POLYETHER_POLYOL": "Flexible slabstock polyol WE DDP",
}


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def monthly_changes(series: list[dict[date, float]]) -> dict[date, float]:
    """Month -> the move into that month, from the first series that holds it and the month before.

    The market's own months are the first series. What crosses from another market is the move, so
    the two levels are never mixed.
    """
    changes: dict[date, float] = {}
    for months in series:
        for day, value in months.items():
            before = date(day.year - (day.month == 1), (day.month - 2) % 12 + 1, 1)
            if day not in changes and before in months:
                changes[day] = value - months[before]
    return changes


def chain(published: dict[date, float], card: dict[date, float],
          beside: list[dict[date, float]] | None = None
          ) -> tuple[dict[date, float], date, float] | None:
    """(the chained line ending on the card's level, the card's latest month, the card's price).

    `beside` is the other European markets for the same chemical, whose moves stand in where the
    card has none. All inputs are monthly, keyed by the first of the month, in the same currency.
    None when the published series or the card is empty.
    """
    if not published or not card:
        return None
    changes = monthly_changes([card, *(beside or [])])
    line = dict(sorted(published.items()))
    last = max(line)
    month = _next_month(last)
    while month in changes:
        line[month] = line[last] + changes[month]
        last, month = month, _next_month(month)
    anchor = max(card)
    anchor_value = card[anchor]
    if anchor in line:
        shift = anchor_value - line[anchor]
        line = {day: value + shift for day, value in line.items()}
    return line, anchor, anchor_value

# What a chained month is filed as. Not a publisher: these months are the published reference series
# carried onto this market's level, so they say so rather than borrowing a source id that would make
# them look collected.
CHAINED_SOURCE = "europe_chain"

# DDP is the published series itself and DAP is the delivered purchase history, each a market with
# its own months (Stefan, 23 September 2026). Neither is chained to anything.
EXEMPT_BASES = frozenset({"DDP", "DAP"})


def continued(session, identity, rows, *, rows_for):
    """The months this market does not hold, taken from the published series (masterplan F).

    **This is the whole of F.** The chain used to be built in the `/forecast` handler and handed to
    `forecast_identity` as a `chained=` argument, which gave it to one model - `weighted_error` -
    and to no governed run at all, because the governed path never passes through that handler. So
    Stefan's point 4 reached one method of eighteen, three markets of 89, and no governed forecast.

    Built here and returned as ROWS, it goes in through `store_series.forecast_rows`, which is the
    one door every forecast comes through: the page, the governed production run, the review job and
    the overrides. Every method fits on the same history, and the request carries no special case.

    Only the months BEFORE the market's own earliest month are returned. The market's own readings
    are untouched, so the latest print, the current price and the chart are exactly as they were -
    what changes is how far back the history reaches. The levels agree at the join by construction:
    `chain` continues the published series with this market's own moves and then shifts the whole
    line onto the market's price.

    `rows_for(identity_id)` fetches another Europe market's rows; passed in rather than imported, so
    this module does not import the module that calls it.
    """
    from app.db.models import MarketIdentityModel, ReferenceObservationModel, ReferenceSeriesModel
    from app.forecasting.service import MONTHLY, bucket
    from app.fx.rates import cross_rate, rate_table
    from sqlalchemy import select

    label = PUBLISHED.get(str(getattr(identity, "product_family", "") or "").upper())
    if str(getattr(identity, "market_geography", "") or "") != "Europe" or not label:
        return []
    if str(getattr(identity, "commercial_basis", "") or "").upper() in EXEMPT_BASES:
        return []
    card = bucket(rows, MONTHLY)
    if not card:
        return []

    beside = []
    for other in session.scalars(
            select(MarketIdentityModel)
            .where(MarketIdentityModel.product_family == identity.product_family)
            .where(MarketIdentityModel.market_geography == "Europe")
            .where(MarketIdentityModel.observation_type == identity.observation_type)):
        if other.id == identity.id:
            continue
        if str(other.commercial_basis or "").upper() in EXEMPT_BASES:
            continue
        beside.append(bucket(rows_for(other.id), MONTHLY))

    series = next((row for row in session.scalars(
        select(ReferenceSeriesModel).where(ReferenceSeriesModel.label == label))
        if not row.is_index), None)
    if series is None:
        return []
    observations = list(session.scalars(
        select(ReferenceObservationModel)
        .where(ReferenceObservationModel.series_id == series.series_id)))
    table = rate_table(session)
    held_in = str(series.unit or "").split("/", 1)[0].strip().upper()
    target = str(getattr(identity, "currency", "") or "").upper()
    published: dict = {}
    for row in observations:
        day = row.observation_date
        day = day.date() if hasattr(day, "date") else day
        rate = cross_rate(table, day, held_in, target)
        if rate is None:
            continue
        published[date(day.year, day.month, 1)] = float(row.value) * rate[0]
    if not published:
        return []

    answer = chain(published, card, beside)
    if answer is None:
        return []
    line, _anchor_period, _anchor_price = answer
    first_of_its_own = min(card)
    return [
        {"source_id": CHAINED_SOURCE, "observation_date": month,
         "native_price": float(value),
         # A chained month could be read as soon as the published series held it. The published
         # series is monthly and arrives with the month, so the month itself is the honest answer -
         # the rolling backtest reads this and must not be told a 2014 month was knowable in 2026.
         "available_from": month}
        for month, value in sorted(line.items())
        if month < first_of_its_own
    ]
