"""Readings that did not become markets, kept with the publisher's own words.

Masterplan D. A value outside the vocabulary does not mint a market - and it is not dropped either.
The reading is written here with what the publisher printed, the document it came from, and the
field that stopped it, and Data & Sources shows it. Nothing is silently lost and nothing silently
becomes a market.

**One row per refusal, not per reading.** A document import that misreads a column misreads it on
every row, and a connector that sends an unknown price kind sends it every day; a row per reading
would bury the one fact a reader needs - *this publisher writes this value and we do not know it* -
under a thousand copies of it. So the row is keyed on (source, field, value) and counts how many
readings it has stopped, with the first and last date and one example reading kept whole. The
example is what a reader needs to judge it; the count is what tells them whether it matters.

**It is a queue, not a bin.** Naming the value is the resolution: add it to
`identity_vocabulary.RAW_MATERIALS`, or fix the connector, and the next collection writes the market
and the reading. `settle` marks the row so the panel shows what is still open without losing the
record of what was refused and when.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import QuarantinedReadingModel
from raw_material_forecasting.identity_vocabulary import Refusal


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day(value: Any) -> str:
    return str(value or "")[:10]


def record(session: Session, *, source_id: str, refusals: Sequence[Refusal],
           reading: dict[str, Any], document: str = "", page: str = "",
           seen_on: Any = None) -> list[QuarantinedReadingModel]:
    """Write or update one row per refusal. Returns the rows touched.

    `reading` is the record's own data, kept whole on the first refusal of its kind - the price, the
    dates, the geography and the basis, in the publisher's words, so a reader can judge the value
    without going back to the document. `document` and `page` say where to go back to when they
    want to.
    """
    out = []
    when = _day(seen_on) or _day(_now().date().isoformat())
    for refusal in refusals or ():
        row = session.scalar(
            select(QuarantinedReadingModel).where(
                QuarantinedReadingModel.source_id == str(source_id or ""),
                QuarantinedReadingModel.field == refusal.field,
                QuarantinedReadingModel.value == refusal.value))
        if row is None:
            row = QuarantinedReadingModel(
                source_id=str(source_id or ""), field=refusal.field, value=refusal.value,
                reason=refusal.reason, example=dict(reading or {}),
                document=str(document or ""), page=str(page or ""),
                readings=1, first_seen=when, last_seen=when)
            session.add(row)
        else:
            # The reason can change under a row - a family that was "not priced" becomes
            # "ambiguous" when it is named in AMBIGUOUS_FAMILIES - and the newest reason is the
            # true one. The example is kept as first read, because that is the one a reader may
            # already have looked at.
            row.reason = refusal.reason
            row.readings = int(row.readings or 0) + 1
            row.last_seen = when
            if not row.document:
                row.document, row.page = str(document or ""), str(page or "")
            row.settled_at = None
        out.append(row)
    if out:
        session.flush()
    return out


def settle(session: Session, *, source_id: str, field: str, value: str, note: str = "") -> bool:
    """Mark one refusal dealt with. False when there is no such row."""
    row = session.scalar(
        select(QuarantinedReadingModel).where(
            QuarantinedReadingModel.source_id == str(source_id or ""),
            QuarantinedReadingModel.field == str(field or ""),
            QuarantinedReadingModel.value == str(value or "")))
    if row is None:
        return False
    row.settled_at = _now()
    row.note = str(note or "")
    session.flush()
    return True


def open_rows(session: Session) -> list[QuarantinedReadingModel]:
    """Everything still refused, worst first: most readings stopped, then most recently seen."""
    rows = list(session.scalars(
        select(QuarantinedReadingModel).where(QuarantinedReadingModel.settled_at.is_(None))))
    return sorted(rows, key=lambda row: (-int(row.readings or 0), row.last_seen or ""),
                  reverse=False)


def as_rows(rows: Iterable[QuarantinedReadingModel]) -> list[dict[str, Any]]:
    """The queue as /quarantine serves it and the page prints it."""
    return [{
        "source_id": row.source_id,
        "field": row.field,
        "value": row.value,
        "reason": row.reason,
        "readings": int(row.readings or 0),
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
        "document": row.document,
        "page": row.page,
        "example": row.example or {},
        "settled_at": row.settled_at.isoformat() if row.settled_at else None,
        "note": row.note or "",
    } for row in rows]
