"""Repair reference points that were built from more than one day's readings.

A reference dated 4 September must be built from the readings published on 4 September. Three of the
91 points in the store were not: the worst averaged 30 readings running from 5 January 2024 to
3 September 2026 and stored the result, CNY 14,900, as that single day's market price - a
two-and-a-half-year mean wearing a date, about CNY 1,900 below the day's own readings.

The rule that prevents it (`select_latest_daily_contributors`, "dates are never blended") was written
on 7 September 2026 at 11:23 UTC. The two worst points were calculated the same morning at 10:44,
thirty-nine minutes earlier, and every one of the 87 points calculated since is clean. So the builder
is sound and the store still carries the rows it wrote before. This recomputes them.

Nothing is deleted. A repaired day gets a new version, the same way any recalculation does, so the
record of what was published and when survives. A day whose own readings can no longer be resolved is
left exactly as it is and reported: a reference that cannot be recomputed from evidence is not one to
replace with a guess.

This pass and `rebuild_reference_series` write the same column, so they have to agree on what the
value is. They did not. This one took a plain mean of the day's readings; the builder takes one value
per source and strips China's 13% VAT from a published domestic assessment. On the TDI China series
that is the whole difference between 16,597.11 and 14,687.71 - and with an undated reading in the
store each pass thought the other's point was wrong and rewrote it, four versions in three seconds,
with the banner showing whichever landed last.

So the arithmetic lives in one place now. This pass finds the days that need recomputing and calls
`consolidated_value` for the answer, the same function the builder calls.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketIdentityModel, MarketReferencePointModel, PriceObservationModel
from app.reference.daily import _datetime_value
from app.reference.series import _rights_by_source, consolidated_value, contributors_by_date


def _day_of(value: Any) -> date | None:
    stamp = _datetime_value(value)
    return stamp.date() if stamp is not None else None


def _latest_versions(session: Session) -> dict[tuple[str, Any], MarketReferencePointModel]:
    """The version of each day that is actually served. Earlier versions are history."""
    latest: dict[tuple[str, Any], MarketReferencePointModel] = {}
    for point in session.scalars(select(MarketReferencePointModel)):
        key = (point.identity_id, point.reference_date)
        held = latest.get(key)
        if held is None or point.version > held.version:
            latest[key] = point
    return latest


def blended_points(session: Session) -> list[MarketReferencePointModel]:
    """Days whose current value was not built from that day's readings.

    Only the version in force counts. A superseded version stays in the store as the record of what
    was published at the time, and re-flagging it forever would make the repair look like it never
    finished.
    """
    observations = {row.id: row for row in session.scalars(select(PriceObservationModel))}
    blended: list[MarketReferencePointModel] = []
    for point in _latest_versions(session).values():
        day = _day_of(point.reference_date)
        ids = list(point.observation_ids or [])
        if day is None or not ids:
            continue
        rows = [observations[obs_id] for obs_id in ids if obs_id in observations]
        if len(rows) != len(ids):
            # An input that cannot be resolved is an unverifiable composition, which is treated the
            # same way as a bad one rather than assumed to be fine.
            blended.append(point)
            continue
        if any(_day_of(row.observation_date) != day for row in rows):
            blended.append(point)
    return blended


def repair_blended_references(session_factory) -> dict:
    """Recompute every blended point from its own day's readings. Idempotent."""
    repaired: list[dict] = []
    unrepairable: list[dict] = []
    with session_factory() as session:
        points = blended_points(session)
        if not points:
            return {"status": "ok", "blended": 0, "repaired": [], "unrepairable": []}

        rights = _rights_by_source(session)
        for point in points:
            day = _day_of(point.reference_date)
            identity = session.get(MarketIdentityModel, point.identity_id)
            rows = list(session.scalars(
                select(PriceObservationModel).where(
                    PriceObservationModel.identity_id == point.identity_id)))
            # The same grouping the builder uses, so both passes agree on which readings belong to
            # a day and which of a source's several readings on it stands.
            contributors = contributors_by_date(rows).get(day) or []
            if not contributors or identity is None:
                unrepairable.append({"identity_id": point.identity_id, "reference_date": str(point.reference_date),
                                     "version": point.version, "reason": "no readings on that day"})
                continue
            value = consolidated_value(contributors, identity, rights)
            observation_ids = sorted(str(row.id) for row in contributors)
            if point.reference_value == value and list(point.observation_ids or []) == observation_ids:
                continue
            from app.persistence.service import _now

            session.add(MarketReferencePointModel(
                identity_id=point.identity_id,
                reference_date=point.reference_date,
                reference_value=value,
                version=point.version + 1,
                observation_ids=observation_ids,
                calculated_at=_now(),
            ))
            repaired.append({"identity_id": point.identity_id, "reference_date": str(point.reference_date),
                             "was": float(point.reference_value), "now": round(value, 2),
                             "readings": len(observation_ids), "version": point.version + 1})
        session.commit()
    return {"status": "ok", "blended": len(points), "repaired": repaired, "unrepairable": unrepairable}
