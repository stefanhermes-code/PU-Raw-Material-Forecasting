from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.sources.registry import SourceRegistry


_REFRESH_INTERVALS = {
    "HOURLY": timedelta(hours=1),
    "DAILY": timedelta(days=1),
    "WEEKLY": timedelta(days=7),
    "MONTHLY": timedelta(days=30),
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def due_source_ids(
    registry: SourceRegistry,
    *,
    last_checked: dict[str, datetime | None] | None = None,
    last_successful: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
) -> list[str]:
    approved = registry.due_sources()
    if last_checked is None and last_successful is None:
        return [source.source_id for source in approved]

    current = _utc(now or datetime.now(timezone.utc))
    due: list[str] = []
    for source in approved:
        cadence = source.refresh_frequency.upper()
        if cadence == "MANUAL":
            continue

        if cadence == "DAILY_CHECK":
            last = (last_checked or {}).get(source.source_id)
            if last is None:
                # Legacy callers have only last-successful timestamps. Use them as
                # a temporary proxy until the cloud source registry owns last_checked.
                last = (last_successful or {}).get(source.source_id)
            if last is None or _utc(last).date() < current.date():
                due.append(source.source_id)
            continue

        last = (last_checked or last_successful or {}).get(source.source_id)
        if last is None:
            due.append(source.source_id)
            continue
        interval = _REFRESH_INTERVALS.get(cadence)
        if interval is None:
            continue
        if current - _utc(last) >= interval:
            due.append(source.source_id)
    return due
