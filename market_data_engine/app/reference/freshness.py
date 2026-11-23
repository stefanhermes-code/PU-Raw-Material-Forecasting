from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

DEFAULT_REFERENCE_FRESHNESS_BUSINESS_DAYS = 2


def _date_only(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


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
        value = getattr(row, field, None)
        parsed = _datetime_value(value)
        if parsed is not None:
            return parsed
    return None


def business_day_age(value: Any, cutoff: datetime | date) -> int | None:
    observed = _date_only(value)
    as_of = _date_only(cutoff)
    if observed is None or as_of is None:
        return None
    if observed > as_of:
        return None
    if observed == as_of:
        return 0
    age = 0
    cursor = observed
    while cursor < as_of:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            age += 1
    return age


def _dedupe_key(row: Any) -> tuple[Any, ...]:
    stamp = observation_timestamp(row)
    return (
        getattr(row, "source_id", None),
        getattr(row, "evidence_id", None),
        stamp.isoformat() if stamp is not None else None,
        getattr(row, "price", None),
    )


def dedupe_reference_rows(rows: Iterable[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = _dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def select_fresh_reference_rows(
    rows: Iterable[Any],
    *,
    cutoff: datetime | date,
    max_business_days: int = DEFAULT_REFERENCE_FRESHNESS_BUSINESS_DAYS,
) -> list[Any]:
    selected: list[Any] = []
    for row in dedupe_reference_rows(rows):
        stamp = observation_timestamp(row)
        age = business_day_age(stamp, cutoff)
        if age is None or age > max_business_days:
            continue
        selected.append(row)
    return selected
