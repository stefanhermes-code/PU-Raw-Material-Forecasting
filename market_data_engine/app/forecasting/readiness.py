from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Any


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class ReadinessReport:
    points: int
    first_date: date | None
    last_date: date | None
    span_days: int
    independent_sources: int
    ready: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        out = asdict(self)
        out["first_date"] = self.first_date.isoformat() if self.first_date else None
        out["last_date"] = self.last_date.isoformat() if self.last_date else None
        out["reasons"] = list(self.reasons)
        return out


def assess_readiness(
    observations: Iterable[Mapping[str, Any]],
    *,
    min_points: int = 24,
    min_span_days: int = 540,
    min_sources: int = 2,
    independent_sources: int | None = None,
) -> ReadinessReport:
    """How much governed history a market has, and whether it is enough.

    `independent_sources` overrides the count taken from the rows, and a caller holding the
    consolidated series has to pass it. Consolidation is the step that collapses many publishers
    into one governed value, and it stamps every row it produces with a single literal source id -
    so counting `source_id` over those rows counts the consolidation, never the publishers, and
    returns 1 for ever. The threshold is 2.

    `/readiness` was fixed for this on 19 September 2026 by overriding the count in the route; the
    backtest behind the Forecast Performance controls was not, so its gate could not pass for any
    consolidated market and the section was empty on every one of them while the gate above it
    read READY.
    """
    rows = [row for row in observations if row.get("observation_date") and row.get("native_price") is not None]
    dates = sorted(_date(row["observation_date"]) for row in rows)
    first = dates[0] if dates else None
    last = dates[-1] if dates else None
    span = (last - first).days if first and last else 0
    sources = (len({str(row.get("source_id")) for row in rows if row.get("source_id")})
               if independent_sources is None else int(independent_sources))
    reasons: list[str] = []
    if len(rows) < min_points:
        reasons.append(f"points {len(rows)} < {min_points}")
    if span < min_span_days:
        reasons.append(f"span_days {span} < {min_span_days}")
    if sources < min_sources:
        reasons.append(f"independent_sources {sources} < {min_sources}")
    return ReadinessReport(len(rows), first, last, span, sources, not reasons, tuple(reasons))
