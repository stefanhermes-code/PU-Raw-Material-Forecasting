"""ECB daily reference rates via frankfurter.dev (public, no key; EUR base).

Governance: rates are stored per date like any other evidence; a price is converted
with the rate valid on its own observation date (last ECB fixing on or before it).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging

import httpx
from sqlalchemy import func, select

from app.db.models import FxRateModel

log = logging.getLogger(__name__)

FRANKFURTER = "https://api.frankfurter.dev/v1"
# THB stays here although it is not a currency a reader can choose (Stefan, 27 October 2026):
# KGI's Thai purchases are paid in baht, and without the fixing they cannot be read in any
# currency at all.
DEFAULT_CURRENCIES: tuple[str, ...] = ("USD", "CNY", "THB", "GBP", "JPY")
# Back to the euro's first fixing, 4 January 1999. It was 2014, where HTC's own purchase history
# starts; the customs history (Eurostat from 1999, UN Comtrade from 2000) and the ICIS South-East Asia
# years from 2010 reach further, and on 13 October 2026 5,546 of 40,308 readings, all dated 1999 to
# 2013, had a value only in the currency they were published in. The ECB quotes USD and THB from
# 4 January 1999 and CNY from 13 January 2000 (checked against the provider that day), so readings
# from 1999 stay without a CNY value.
HISTORY_START = date(1999, 1, 4)


def _fetch_range(start: date, end: date, currencies: tuple[str, ...]) -> dict[str, dict[str, float]]:
    url = f"{FRANKFURTER}/{start.isoformat()}..{end.isoformat()}"
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url, params={"from": "EUR", "to": ",".join(currencies)})
        response.raise_for_status()
        payload = response.json()
    return payload.get("rates") or {}


def refresh_ecb_rates(session_factory, *, currencies: tuple[str, ...] = DEFAULT_CURRENCIES, today: date | None = None) -> dict:
    """Fetch missing ECB fixings and store them. Never raises: returns a status dict."""
    today = today or datetime.now(timezone.utc).date()
    with session_factory() as session:
        latest = session.scalar(select(func.max(FxRateModel.rate_date)).where(FxRateModel.base == "EUR"))
        latest_date = latest.date() if isinstance(latest, datetime) else (latest if isinstance(latest, date) else None)
        # Re-fetch the last few days so late ECB revisions/holidays are covered.
        start = HISTORY_START if latest_date is None else max(HISTORY_START, latest_date - timedelta(days=5))
        try:
            rates = _fetch_range(start, today, currencies)
        except Exception as exc:  # network / provider failure: keep what we have
            log.warning("ECB rate refresh failed: %s", exc)
            return {"status": "unavailable", "error": str(exc), "latest_date": latest_date.isoformat() if latest_date else None, "inserted": 0}

        existing = {
            (row.rate_date.date() if isinstance(row.rate_date, datetime) else row.rate_date, row.currency): row
            for row in session.scalars(select(FxRateModel).where(FxRateModel.rate_date >= datetime(start.year, start.month, start.day, tzinfo=timezone.utc)))
        }
        inserted = 0
        for day, per_currency in rates.items():
            day_obj = date.fromisoformat(day)
            for currency, rate in per_currency.items():
                row = existing.get((day_obj, currency))
                if row is None:
                    session.add(FxRateModel(
                        rate_date=datetime(day_obj.year, day_obj.month, day_obj.day, tzinfo=timezone.utc),
                        base="EUR", currency=currency, rate=float(rate), source="ECB",
                    ))
                    inserted += 1
                elif abs(float(row.rate) - float(rate)) > 1e-9:
                    row.rate = float(rate)
        session.commit()
        latest = session.scalar(select(func.max(FxRateModel.rate_date)).where(FxRateModel.base == "EUR"))
        latest_date = latest.date() if isinstance(latest, datetime) else latest
    return {"status": "ok", "inserted": inserted, "latest_date": latest_date.isoformat() if latest_date else None, "currencies": list(currencies)}


def backfill_ecb_rates(session_factory, *, start: date = HISTORY_START,
                       currencies: tuple[str, ...] = DEFAULT_CURRENCIES) -> dict:
    """Fill in history before the earliest rate we hold.

    `refresh_ecb_rates` only ever reaches back five days from the newest fixing, which is right for
    keeping current but cannot deepen an existing store. Extending the history needs its own path,
    and it is a one-off: once 1999 is in, there is nothing earlier to fetch.
    """
    with session_factory() as session:
        earliest = session.scalar(select(func.min(FxRateModel.rate_date)).where(FxRateModel.base == "EUR"))
    earliest_date = earliest.date() if isinstance(earliest, datetime) else (earliest if isinstance(earliest, date) else None)
    if earliest_date is not None and earliest_date <= start:
        return {"status": "complete", "earliest": earliest_date.isoformat(), "inserted": 0}

    end = (earliest_date - timedelta(days=1)) if earliest_date else datetime.now(timezone.utc).date()
    try:
        rates = _fetch_range(start, end, currencies)
    except Exception as exc:  # network / provider failure: keep what we have
        log.warning("ECB backfill failed: %s", exc)
        return {"status": "unavailable", "error": str(exc), "inserted": 0}

    inserted = 0
    with session_factory() as session:
        existing = {
            (row.rate_date.date() if isinstance(row.rate_date, datetime) else row.rate_date, row.currency)
            for row in session.scalars(select(FxRateModel))
        }
        for day, per_currency in rates.items():
            day_obj = date.fromisoformat(day)
            for currency, rate in per_currency.items():
                if (day_obj, currency) in existing:
                    continue
                session.add(FxRateModel(
                    rate_date=datetime(day_obj.year, day_obj.month, day_obj.day, tzinfo=timezone.utc),
                    base="EUR", currency=currency, rate=float(rate), source="ECB",
                ))
                inserted += 1
        session.commit()
    return {"status": "ok", "start": start.isoformat(), "end": end.isoformat(), "inserted": inserted}


def list_rates(session_factory, *, currencies: tuple[str, ...] | None = None) -> list[dict]:
    with session_factory() as session:
        query = select(FxRateModel).where(FxRateModel.base == "EUR").order_by(FxRateModel.rate_date, FxRateModel.currency)
        if currencies:
            query = query.where(FxRateModel.currency.in_(list(currencies)))
        return [
            {"date": (row.rate_date.date() if isinstance(row.rate_date, datetime) else row.rate_date).isoformat(),
             "base": row.base, "currency": row.currency, "rate": float(row.rate), "source": row.source}
            for row in session.scalars(query)
        ]


def is_stale(session_factory, *, today: date | None = None, max_age_days: int = 4) -> bool:
    today = today or datetime.now(timezone.utc).date()
    with session_factory() as session:
        latest = session.scalar(select(func.max(FxRateModel.rate_date)).where(FxRateModel.base == "EUR"))
    if latest is None:
        return True
    latest_date = latest.date() if isinstance(latest, datetime) else latest
    return (today - latest_date).days > max_age_days
