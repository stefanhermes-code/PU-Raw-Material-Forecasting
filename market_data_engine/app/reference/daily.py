from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable


def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def observation_timestamp(row: Any) -> datetime | None:
    for field in ("observation_date", "publication_date", "available_from"):
        parsed = _datetime_value(getattr(row, field, None))
        if parsed is not None:
            return parsed
    return None


def _available_timestamp(row: Any) -> datetime:
    parsed = _datetime_value(getattr(row, "available_from", None))
    if parsed is not None:
        return parsed
    parsed = _datetime_value(getattr(row, "publication_date", None))
    if parsed is not None:
        return parsed
    parsed = observation_timestamp(row)
    if parsed is not None:
        return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def select_latest_daily_contributors(
    rows: Iterable[Any],
    *,
    cutoff: datetime | date | None = None,
) -> list[Any]:
    """Return one latest value per source from the most recent observation date.

    Dates are never blended. When a source has more than one observation on the
    latest date, its latest available observation is used.
    """
    cutoff_dt = _datetime_value(cutoff) if cutoff is not None else None
    eligible: list[tuple[Any, datetime]] = []
    for row in rows:
        observed = observation_timestamp(row)
        if observed is None or getattr(row, "price", None) is None:
            continue
        if cutoff_dt is not None:
            if observed > cutoff_dt:
                continue
            available = _available_timestamp(row)
            if available > cutoff_dt:
                continue
        eligible.append((row, observed))

    if not eligible:
        return []

    latest_day = max(observed.date() for _row, observed in eligible)
    same_day = [(row, observed) for row, observed in eligible if observed.date() == latest_day]

    latest_by_source: dict[str, Any] = {}
    for row, _observed in same_day:
        source_id = str(getattr(row, "source_id", None) or "")
        if not source_id:
            continue
        current = latest_by_source.get(source_id)
        if current is None:
            latest_by_source[source_id] = row
            continue
        current_key = (_available_timestamp(current), str(getattr(current, "id", "")))
        candidate_key = (_available_timestamp(row), str(getattr(row, "id", "")))
        if candidate_key > current_key:
            latest_by_source[source_id] = row

    return [latest_by_source[source_id] for source_id in sorted(latest_by_source)]


def arithmetic_mean_reference(rows: Iterable[Any]) -> float:
    prices = [float(getattr(row, "price")) for row in rows if getattr(row, "price", None) is not None]
    if not prices:
        raise ValueError("arithmetic_mean_reference requires at least one price")
    return sum(prices) / len(prices)
