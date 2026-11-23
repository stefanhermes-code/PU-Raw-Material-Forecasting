"""The series the forecast is computed on, read from the store (moved out of `app.main`, WP2).

Moved so that the offline review job (`app.forecasting.review_job`) reads EXACTLY the rows the
interactive `/forecast` endpoint reads - the consolidated series in force, net of tax, with each
point's availability - rather than a second reading that could drift from the first. `app.main`
calls these; nothing here imports it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models import MarketIdentityModel, MarketReferencePointModel, PriceObservationModel


# The one name the forecast's own readings are filed under. They are not a publisher's prints;
# they are the consolidated series, which is one value per day whoever contributed to it.
CONSOLIDATED_SOURCE = "market_reference_point"


def forecast_rows(session_factory, identity_id: str, *, all_versions: bool = False) -> list[dict]:
    """The governed series the forecast is computed on.

    This read the raw price observations, and that was wrong in a way that showed on the screen.
    The raw rows are what publishers printed: several per day, each still carrying China's 13%
    VAT, and a publisher that prints four times a day counted four times. The consolidated
    series is the answer to all three - one value per source, then the mean, net of VAT - and it
    is the number the Current Market page shows and the whole engine exists to produce.

    Feeding the models the raw rows put two prices for September on one screen: 16,866.67 on the
    chart, from the raw prints with the tax inside, and 14,687.71 in the banner, from the
    consolidated series. Same month, same market, 13% apart.

    An identity whose consolidated series has not been built yet falls back to its raw rows, so
    a new market still gets a forecast rather than a blank page; that series will supersede it
    the moment it exists.

    **And the Europe chain is part of the series here, not an argument to one model** (masterplan F).
    Stefan's point 4 - series glued to close gaps - was built inside the `/forecast` handler and
    handed to `weighted_error` alone, so it reached one method of eighteen, three markets of 89 and
    no governed forecast at all, because the governed run never passes through that handler. This
    function is the one door every forecast comes through, so gluing here is what makes the glue
    reach everything: the page, the governed production run, the review job and the overrides.
    Only months the market does not hold itself are added; its own readings are untouched.
    """
    with session_factory() as session:
        consolidated = consolidated_rows(session, identity_id, all_versions=all_versions)
        if consolidated:
            return _with_the_chain(session, identity_id, consolidated, session_factory,
                                   all_versions=all_versions)
        return _raw_rows(session, identity_id, session_factory, all_versions=all_versions)


def _raw_rows(session, identity_id: str, session_factory, *, all_versions: bool = False) -> list[dict]:
    """A market with no consolidated series yet, read from its own prints and then glued.

    Split out so the glue runs on the session already open rather than taking a second one.
    """
    rows = list(
        session.scalars(
            select(PriceObservationModel)
            .where(PriceObservationModel.identity_id == identity_id)
            .order_by(PriceObservationModel.observation_date, PriceObservationModel.id)
        )
    )
    from app.reference.series import _rights_by_source, net_price

    identity = session.get(MarketIdentityModel, identity_id)
    rights = _rights_by_source(session)
    # Net of tax, like the consolidated series this stands in for.
    #
    # The raw rows are what a publisher printed, and a Chinese domestic assessment is printed
    # with the 13% VAT inside it. Handing them to the models gross while the banner beside the
    # chart reads the net governed value is the same 13% divergence that put two prices for one
    # September on one screen. It was fixed for the consolidated path and left here, where it
    # waits for the next market that has no consolidated series yet - POP and HR, when they come.
    own = [
        {
            "source_id": row.source_id,
            "observation_date": row.observation_date,
            "native_price": net_price(row, identity, rights) if identity is not None else float(row.price),
            # v0.5.60 (gate finding 2): when the print could first be read. Without it a backtest
            # treated a raw row as readable on the date it refers to - months early for customs.
            "available_from": row.available_from,
        }
        for row in rows
        if row.observation_date is not None and row.price is not None
    ]
    # The glue belongs on BOTH paths, and it was on one.
    #
    # `_with_the_chain` was reached only when a consolidated series existed, so a market with none
    # returned here ungleued. That is precisely backwards: a market with no consolidated series is a
    # market whose collection has only just begun, which is the one that most needs the record it
    # had before. Measured 3 November 2026 - Aliphatic Polyester Polyol China EXW, nine readings
    # old, sat in front of 41 months of its own earlier record and got none of them, while Polyether
    # Polyol China FOB, which does have a consolidated series, was glued correctly.
    #
    # On the session already open above, never a new one: a connection taken while holding a
    # connection is how a pool of fifteen empties.
    if not own:
        return own
    return _with_the_chain(session, identity_id, own, session_factory, all_versions=all_versions)


def consolidated_rows(session, identity_id: str, *, all_versions: bool = False) -> list[dict]:
    """One row per date: the version in force, and nothing that it superseded.

    A revision is written as a new version beside the one it replaces, so reading the table as
    it stands would hand the models both the old value and the new one for the same day.

    `all_versions=True` (the backtest, v0.5.60, gate finding 6) returns every version instead, each
    with its own availability and its `version`: the first stored version is knowable when its prints
    arrived; a LATER version only from when it was calculated, because a revision did not exist
    before then. `history.visible_rows` keeps, per date, the newest version a reader on the cutoff
    could have had - so an earlier origin sees the value as it stood then, not the later revision.
    """
    in_force: dict = {}
    versions: dict = {}
    for point in session.scalars(
            select(MarketReferencePointModel)
            .where(MarketReferencePointModel.identity_id == identity_id)
            .order_by(MarketReferencePointModel.version)):
        stamp = point.reference_date
        if stamp is None:
            continue
        day = stamp.date() if hasattr(stamp, "date") else stamp
        in_force[day] = point
        versions.setdefault(day, []).append(point)
    # v0.5.60: every point's observations' availability in ONE query per identity (chunked), not one
    # query per point. Against Supabase in Singapore the per-point form cost ~20 minutes for the
    # review's 330 markets; the answer is the same.
    chosen = [p for ps in versions.values() for p in ps] if all_versions else list(in_force.values())
    wanted = sorted({str(i) for p in chosen if p.reference_value is not None
                     for i in (p.observation_ids or ())})
    moments: dict[str, Any] = {}
    for start in range(0, len(wanted), 1000):
        chunk = wanted[start:start + 1000]
        for obs_id, moment in session.execute(
                select(PriceObservationModel.id, PriceObservationModel.available_from)
                .where(PriceObservationModel.id.in_(chunk))):
            if moment is not None:
                moments[str(obs_id)] = moment
    if all_versions:
        out = []
        for _day, points in sorted(versions.items()):
            for position, point in enumerate(points):
                if point.reference_value is None:
                    continue
                known = _available_from_known(point, moments)
                if position > 0 and point.calculated_at is not None:
                    known = max(_aware(known), _aware(point.calculated_at))
                out.append({"source_id": CONSOLIDATED_SOURCE, "observation_date": point.reference_date,
                            "native_price": float(point.reference_value), "available_from": known,
                            "version": point.version})
        return out
    return [
        {
            "source_id": CONSOLIDATED_SOURCE,
            "observation_date": point.reference_date,
            "native_price": float(point.reference_value),
            # When this consolidated value could first have been read. A governed point is
            # knowable once the prints behind it have arrived, so it is the latest
            # availability among its own observations. The rolling backtest behind the bands
            # reads this; the live forecast does not need it, because the store only ever
            # holds what has already arrived.
            "available_from": _available_from_known(point, moments),
        }
        for _day, point in sorted(in_force.items())
        if point.reference_value is not None
    ]


def _aware(moment):
    """A datetime comparable with another: naive values read back from SQLite are taken as UTC."""
    from datetime import date as _date, datetime as _datetime, timezone as _tz
    if isinstance(moment, _datetime):
        return moment if moment.tzinfo else moment.replace(tzinfo=_tz.utc)
    if isinstance(moment, _date):
        return _datetime(moment.year, moment.month, moment.day, tzinfo=_tz.utc)
    return moment


def _available_from_known(point, moments: dict) -> Any:
    """`available_from` with the observations' moments already fetched; the same rule, no query."""
    found = [moments[str(i)] for i in (point.observation_ids or ()) if str(i) in moments]
    if found:
        return max(found)
    return point.calculated_at or point.reference_date


def available_from(session, point):
    """The moment the last print behind a consolidated point became available.

    Falls back to when the point was calculated, and then to the date it refers to. The last
    fallback is the pre-existing behaviour and says "assume it was knowable immediately", which
    is right for a publisher that prints on the day and wrong for a customs series - so it is a
    last resort rather than a default.
    """
    ids = list(point.observation_ids or ())
    if ids:
        moments = [
            row for row in session.scalars(
                select(PriceObservationModel.available_from)
                .where(PriceObservationModel.id.in_(ids)))
            if row is not None
        ]
        if moments:
            return max(moments)
    return point.calculated_at or point.reference_date

def _with_the_chain(session, identity_id: str, rows: list[dict], session_factory,
                    *, all_versions: bool = False) -> list[dict]:
    """`rows`, with the published Europe series in front of them where there is one.

    Returns `rows` unchanged for every market that is not one of the three European chemicals with a
    published reference series - which is all but three of the 89 the forecast page offers. The
    chain's own months carry `source_id = europe_chain`, so anything downstream that cares can tell
    them from the market's own readings.

    A failure here must not take a forecast off the page: a market with no chain is the normal case,
    and a market whose chain cannot be built is no worse off than it was before F.
    """
    from app.forecasting import earlier_record, europe_chain

    identity = session.get(MarketIdentityModel, identity_id)
    if identity is None:
        return rows
    fetch = lambda other: forecast_rows(session_factory, other, all_versions=all_versions)
    try:
        before = europe_chain.continued(session, identity, rows, rows_for=fetch)
    except Exception:                      # pragma: no cover - the market keeps its own history
        before = []
    if not before:
        # The market's own earlier record, where it has one and the published chain did not answer.
        # Stefan, 3 November 2026: "Of course it can be used as history for forecasting." The two
        # are asked in order rather than added together because they answer the same question - what
        # came before the months we hold - and the published European series, where there is one, is
        # the better answer: it is continuous to last month, while an earlier record is by
        # definition separated from us by a hole.
        try:
            before = earlier_record.continued(session, identity, rows, rows_for=fetch)
        except Exception:                  # pragma: no cover - the market keeps its own history
            before = []
    return [*before, *rows] if before else rows
