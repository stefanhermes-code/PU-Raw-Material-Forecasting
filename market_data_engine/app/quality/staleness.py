"""A feed that keeps publishing the same number is broken, and nothing said so.

The store already notices a source that stops arriving: `classify_source` calls it STALE DATA when
the newest reading is old. It did not notice the other kind, where the dates keep advancing and the
value never moves.

That is what a trader's standing offer looks like when it is read as a market: ChemicalBook printed
11,960 nine times running through December 2025, then jumped to 13,890 and printed that twice. Every
reading arrived on time. Nothing was flagged. On 19 September 2026 the same listing sat 70% above
the market and had done for months.

What this does NOT do is substitute a value. The module this idea came from computed a synthetic
price when a feed looked frozen; a made-up number in place of a missing one is worse than the gap,
because the gap is visible. This raises an issue and leaves the data exactly as it was.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

# A published assessment that has not moved in this many consecutive readings is not being assessed.
MIN_REPEATS = 5

# ... and has covered at least this long. Five readings inside one week is a quiet week; five
# readings over a month is a listing nobody is maintaining.
MIN_SPAN_DAYS = 14


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class FrozenRun:
    """One unbroken run of identical prices from one source."""

    source_id: str
    price: float
    readings: int
    first_date: date
    last_date: date

    @property
    def span_days(self) -> int:
        return (self.last_date - self.first_date).days

    def as_details(self) -> dict:
        return {
            "price": self.price,
            "readings": self.readings,
            "first_date": self.first_date.isoformat(),
            "last_date": self.last_date.isoformat(),
            "span_days": self.span_days,
        }

    def as_sentence(self) -> str:
        return (f"{self.readings} consecutive readings at {self.price:,.2f} "
                f"from {self.first_date} to {self.last_date} ({self.span_days} days)")


def frozen_run(rows: Iterable[Mapping[str, Any]], *,
               min_repeats: int = MIN_REPEATS,
               min_span_days: int = MIN_SPAN_DAYS) -> FrozenRun | None:
    """The run of identical prices ending at the most recent reading, if it is long enough.

    Only the run that reaches the present counts. A source that was flat in March and has moved
    since is not frozen; it had a quiet March.
    """
    dated = [(d, row) for row in rows
             if (d := _as_date(row.get("observation_date"))) is not None
             and row.get("price") is not None]
    if not dated:
        return None
    dated.sort(key=lambda pair: pair[0])

    source_id = str(dated[-1][1].get("source_id") or "")
    last_price = float(dated[-1][1]["price"])
    run: list[tuple[date, Mapping[str, Any]]] = []
    for day, row in reversed(dated):
        if float(row["price"]) != last_price:
            break
        if str(row.get("source_id") or "") != source_id:
            break
        run.append((day, row))
    if len(run) < min_repeats:
        return None

    first_day = run[-1][0]
    last_day = run[0][0]
    if (last_day - first_day).days < min_span_days:
        return None
    return FrozenRun(source_id, last_price, len(run), first_day, last_day)
