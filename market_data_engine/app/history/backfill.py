"""What each daily series is missing, and at what cadence the publisher actually keeps.

This module used to do two unrelated things: fetch a publisher's history pages to recover missed
trading days, and report which days a series is missing. The fetching half was removed on
21 October 2026. Stefan: "Delete it."

It had never recovered a day. It could reach exactly one source. Of the 59 source definitions in the scraping registry, 57 have no
reader for their publisher's history pages at all. One, sunsirs_tdi, has one and opted out with
`routine_backfill = False`: between 25 September and 17 October 2026 it fetched 151 weekly snapshot
pages and parsed 0 readings, every fetch a different interstitial rather than a price table. That
leaves echemi_tdi, which would have been asked - and ECHEMI has served an access-verification page
since 15 September 2026, so it returned nothing.

It ran nightly over a 45-day window, spent up to fifteen minutes doing so and wrote
"backfill": {"state": "complete"} into the collection status - which is how it went unnoticed from
the day it was built.

Most of these publishers post today's price and keep no archive, so a day nobody collected is gone.
Reporting the gap is still worth doing, and that is what is left here: /coverage reads it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceObservationModel
from raw_material_forecasting import price_kinds


# ----------------------------------------------------------------------------- coverage

def _weekdays(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _cadence(days: Sequence[date]) -> str:
    """Daily, weekly or monthly, read from the spacing the publisher actually keeps.

    Without this every monthly index is reported as a daily series missing twenty-nine days, which
    is not a gap - it is the publisher's own cadence - and a report that cries wolf on half its rows
    is a report nobody reads.
    """
    if len(days) < 2:
        return "unknown"
    gaps = sorted((later - earlier).days for earlier, later in zip(days, days[1:]))
    median = gaps[len(gaps) // 2]
    if median <= 3:
        return "daily"
    if median <= 10:
        return "weekly"
    return "monthly"


def daily_coverage(session_factory, *, window_days: int = 30, today: date | None = None,
                   observation_type: str | None = None) -> list[dict]:
    """Which trading days each daily series is missing, over the recent window.

    Weekday-based, because these are business-day markets and counting weekends as gaps would make
    every series look broken. A publisher's own holidays still show as gaps, which is the honest
    default: the alternative is a calendar per market that nobody maintains.

    Today is never counted as missing: the day is not over and the daily collection may not have run
    yet. Only completed days can be owed.
    """
    end = today or datetime.now(timezone.utc).date()
    start = end - timedelta(days=window_days)
    expected = [day for day in _weekdays(start, end) if day < end]

    rows: list[dict] = []
    with session_factory() as session:
        # Every kind a customer page may show, unless a caller names one. It used to default to
        # the literal "market_price", which after migration 0058 matched nothing at all and the
        # coverage report went silently empty.
        wanted = ([observation_type] if observation_type is not None
                  else list(price_kinds.PUBLISHED))
        identities = list(session.scalars(
            select(MarketIdentityModel).where(MarketIdentityModel.observation_type.in_(wanted))
        ))
        for identity in identities:
            observations = list(session.scalars(
                select(PriceObservationModel).where(PriceObservationModel.identity_id == identity.id)
            ))
            held = set()
            for observation in observations:
                value = observation.observation_date
                if value is None:
                    continue
                held.add(value.date() if isinstance(value, datetime) else value)
            in_window = sorted(day for day in held if start <= day <= end)
            if not in_window:
                continue
            # Only count gaps from the first day this series was actually seen; a series that started
            # last week has not "missed" the days before it existed.
            since = in_window[0]
            cadence = _cadence(in_window)
            owed = [day for day in expected if day >= since]
            missing = ([day for day in owed if day not in held]
                       if cadence in {"daily", "unknown"} else [])
            rows.append({
                "cadence": cadence,
                "identity_id": identity.id,
                "product_family": identity.product_family,
                "market_geography": identity.market_geography,
                "commercial_basis": identity.commercial_basis,
                "currency": identity.currency,
                "first_seen_in_window": since.isoformat(),
                # Both sides of the ratio count the same days: completed weekdays since this series
                # was first seen. A Saturday post, or today's, is a bonus rather than a day owed, and
                # counting either on one side only would read as "7 of 4".
                "days_held": len([d for d in owed if d in held]),
                "days_posted": len(in_window),
                "days_expected": len(owed),
                "days_missing": len(missing),
                "missing": [day.isoformat() for day in missing],
            })
    rows.sort(key=lambda row: (-row["days_missing"], row["product_family"], str(row["market_geography"])))
    return rows


def coverage_summary(rows: Sequence[dict]) -> dict:
    """One line a caller can act on without reading every series."""
    incomplete = [row for row in rows if row["days_missing"]]
    return {
        "series": len(rows),
        "daily_series": len([row for row in rows if row.get("cadence") == "daily"]),
        "series_with_gaps": len(incomplete),
        "days_missing_total": sum(row["days_missing"] for row in incomplete),
        "worst": incomplete[0]["identity_id"] if incomplete else None,
    }
