"""What a person read off one of HTC Global's own reports, kept where the forecaster looks.

Stefan, 21 October 2026: *"At least it should read the intelligence, or the pricing when it adds
value."*

Until now there were two stores that never met. A briefing imported on the page was written to
`briefing_register.csv` and a special report to `supply_conditions.json`, both files beside the
application, and the Market Intelligence page read them. The forecaster reads neither: its event
counts come from `event_record` in this store, through `review_job.feature_rows`, and every row in
that table had been put there by the scraping lane - 95 of 118 from one publisher. So every document
anyone imported was visible on a page and invisible to every forecast, and no amount of importing
could change that.

This module is the missing half. A development confirmed on the page arrives here and becomes an
event record like any other, dated by what the report said rather than by when it was typed in.

Two things it deliberately does NOT do:

- It does not re-read the document. What arrives is what a person confirmed on screen, the same
  rule `/import` follows for prices: a re-read could store something other than what was approved.
- It does not judge the content. An event with a material this store does not price is still
  stored, because what the forecaster makes of it is for the review to measure. The only rows
  refused are ones that carry no day, since an undated event cannot be placed in a series.

Dedup follows the scraping lane exactly - `(source_id, event_type, headline)` - so re-importing a
document, or importing a corrected copy of it, adds nothing a second time.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select

from app.db.models import EventRecordModel

# The report families, used as the source_id so a stored event says which of HTC Global's own
# reports it was read off. They are not publishers and carry no rights question: these are ours.
BRIEFING = "htc_briefing"
CONDITION = "htc_supply_condition"
MONITOR = "htc_price_monitor"
FAMILIES = (BRIEFING, CONDITION, MONITOR)


def _as_datetime(value: Any) -> datetime | None:
    """The day a report spoke for, as an instant this store can order by."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    for shape in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:len(shape) + 2].strip(), shape).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _already_held(session, source_id: str, event_type: str, headline: str, evidence_id: str,
                  material: str | None, day):
    """The scraping lane's rule, with the material added, because this lane fans out.

    `persistence/service.py` dedups on (source_id, event_type, headline), which is right where it
    is used: one page carries one event of a type, and the same headline on a later fetch of that
    page is the same event. These documents are dated by their authors, so the day is part of the
    key here: a chain report grades TDI in the same words every few days, and without the day
    twenty-one readings would be one. Here one headline becomes one event per
    chemical - "Planned Asian MDI maintenance supports the prompt market" is stored under PURE_MDI
    and under PMDI - so a key without the material would keep the first and silently swallow every
    other chemical the item moved. The forecaster would then count the item for one series and not
    for the other, which is the opposite of why the fan-out exists.

    Found by `test_a_confirmed_development_is_stored_where_the_forecaster_looks` on 21 October 2026,
    which stored two chemicals and got one row back.
    """
    if headline:
        return session.scalar(select(EventRecordModel).where(
            EventRecordModel.source_id == source_id,
            EventRecordModel.event_type == event_type,
            EventRecordModel.material.is_(material) if material is None
            else EventRecordModel.material == material,
            EventRecordModel.available_from == day,
            EventRecordModel.data["headline"].as_string() == headline))
    return session.scalar(select(EventRecordModel).where(
        EventRecordModel.source_id == source_id,
        EventRecordModel.event_type == event_type,
        EventRecordModel.evidence_id == evidence_id))


def receive(session_factory, source_id: str, events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Store what a person confirmed off one report. Nothing is read out of the file here.

    One event per material. A briefing item about three chemicals arrives as three events, because
    `feature_rows` counts events whose `material` matches the series' chemical exactly: an item
    stored once under "PO;POLYETHER_POLYOL" would be counted for neither.
    """
    if source_id not in FAMILIES:
        return {"stored": 0, "already_held": 0, "refused": [
            {"why": f"{source_id} is not one of HTC Global's own report families"}]}

    stored = already = 0
    refused: list[dict[str, Any]] = []
    with session_factory() as session:
        for index, event in enumerate(events or []):
            headline = str(event.get("headline") or "").strip()
            day = _as_datetime(event.get("available_from"))
            if day is None:
                refused.append({"headline": headline[:120], "why": "no day to place it on"})
                continue
            event_type = str(event.get("event_type") or "DEVELOPMENT").upper()
            evidence_id = str(event.get("evidence_id") or "").strip()
            material = str(event.get("material") or "").strip().upper() or None
            if _already_held(session, source_id, event_type, headline, evidence_id,
                             material, day) is not None:
                already += 1
                continue
            data = dict(event.get("data") or {})
            data.setdefault("headline", headline)
            if material:
                data.setdefault("material", material)
            data.setdefault("document", str(event.get("document") or ""))
            data.setdefault("read_by_a_person", True)
            session.add(EventRecordModel(
                source_id=source_id, evidence_id=evidence_id or f"{headline[:60]}#{index}",
                event_type=event_type, material=material,
                state=str(event.get("state") or "ANNOUNCED"),
                data=data, available_from=day))
            session.flush()      # so the next chemical of the same headline sees this one
            stored += 1
        session.commit()
    return {"stored": stored, "already_held": already, "refused": refused}


def held(session_factory, source_id: str = "") -> list[dict[str, Any]]:
    """What this lane has put in the store, for the page that asks whether an import arrived."""
    with session_factory() as session:
        query = select(EventRecordModel).where(EventRecordModel.source_id.in_(FAMILIES))
        if source_id:
            query = query.where(EventRecordModel.source_id == source_id)
        return [{"source_id": row.source_id, "event_type": row.event_type, "material": row.material,
                 "headline": str((row.data or {}).get("headline") or ""),
                 "document": str((row.data or {}).get("document") or ""),
                 "available_from": row.available_from.date().isoformat() if row.available_from else ""}
                for row in session.scalars(query.order_by(EventRecordModel.available_from))]
