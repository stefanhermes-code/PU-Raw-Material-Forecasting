"""Writing down what was claimed, before anybody knows whether it was right.

The application has been computing forecasts on request and throwing them away. That is fine for a
number on a screen and fatal for any statement about how good the numbers are: a claim nobody wrote
down cannot be checked, and the one claim that was made - that the price lands inside the range about
eight times in ten - survived for weeks precisely because nothing on disk could contradict it. When
it was finally measured it was holding about half the time.

So every forecast the engine produces is recorded as a vintage: identity, model, horizon, as-of, the
number, the band, and how much evidence the band rested on. Written once. The outcome is appended
later by `settle`, when the period it named has closed and a real price for that period exists.

Two rules do the work, and both are about making dishonesty require effort rather than merely
discouraging it:

**Append-only.** `record` never updates a row. A second write for the same claim is a duplicate and
is counted as one, not merged. The forecast that was shown on a Tuesday stays exactly as it was
shown, including its mistakes.

**Settling is a separate act.** Recording what happened writes into columns the forecast never
touches. There is no code path that adjusts a forecast in the light of its outcome, because there is
no code path that writes both.

`state` travels with each row because a candidate's forecast and a released forecast are evidence
about different things. Scoring them together would let a model's research history flatter - or
damn - what customers were actually shown.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select

from app.db.models import ForecastVintageModel

RELEASED = "RELEASED"
CANDIDATE = "CANDIDATE"
CONTROL = "CONTROL"

LOW_QUANTILE = "0.1"
HIGH_QUANTILE = "0.9"


def vintage_key(identity_key: str, model_key: str, horizon_weeks: int, as_of: date) -> str:
    """One claim, named by what makes it that claim and nothing else.

    Not by the value: two runs on the same day that disagree are a bug to find, not two vintages to
    keep. Not by a timestamp either, or every page refresh would be a new claim and the store would
    fill with the same forecast a hundred times over.
    """
    return f"{identity_key}|{model_key}|{int(horizon_weeks)}w|{as_of.isoformat()}"


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    text = str(value or "")[:10]
    try:
        return datetime.combine(date.fromisoformat(text), time.min, tzinfo=timezone.utc)
    except ValueError:
        return None


def _state_of(model: dict, released: bool) -> str:
    if model.get("is_control"):
        return CONTROL
    return RELEASED if released else CANDIDATE


def record(session_factory, forecast: dict[str, Any]) -> dict[str, Any]:
    """Store every point of a `forecast_identity` payload as a vintage. Idempotent.

    Takes the payload the endpoint already returns rather than a bespoke argument list, so what is
    recorded is by construction what was served. A recording path that rebuilt the forecast its own
    way could drift from the one on the screen, and the drift would be invisible.
    """
    identity_key = str(forecast.get("identity_key") or "")
    as_of = _as_datetime(forecast.get("as_of"))
    if not identity_key or as_of is None:
        return {"status": "skipped", "reason": "forecast carries no identity or no as-of date",
                "written": 0}

    released = bool(forecast.get("released"))
    chemical = str(forecast.get("chemical") or "")
    currency = str(forecast.get("currency") or "")
    anchor = forecast.get("anchor_value")
    observations = int(forecast.get("observations") or 0)

    written = 0
    already = 0
    with session_factory() as session:
        held = set(session.scalars(select(ForecastVintageModel.vintage_key)))
        for model in forecast.get("models") or ():
            if model.get("status") != "ok":
                continue
            for point in model.get("points") or ():
                period = _as_datetime(point.get("period"))
                if period is None or point.get("value") is None:
                    continue
                key = vintage_key(identity_key, str(model.get("model") or ""),
                                  int(point.get("horizon_weeks") or 0), as_of.date())
                if key in held:
                    already += 1
                    continue
                held.add(key)
                session.add(ForecastVintageModel(
                    vintage_key=key,
                    identity_key=identity_key,
                    model_key=str(model.get("model") or ""),
                    model_version=str(model.get("version") or ""),
                    horizon_weeks=int(point.get("horizon_weeks") or 0),
                    as_of=as_of,
                    chemical=chemical,
                    currency=currency,
                    value=float(point["value"]),
                    quantiles=dict(point.get("quantiles") or {}),
                    period=period,
                    steps=int(point.get("steps") or 1),
                    resolution=str(point.get("resolution") or "M"),
                    exact=bool(point.get("exact")),
                    calibration=dict(point.get("calibration") or {}),
                    observations=observations,
                    anchor_value=float(anchor) if anchor is not None else None,
                    state=_state_of(model, released),
                    metadata_json=dict(model.get("metadata") or {}),
                ))
                written += 1
        session.commit()
    return {"status": "ok", "identity_key": identity_key, "as_of": as_of.date().isoformat(),
            "written": written, "already_present": already}


def due(session_factory, identity_key: str = "", *, on: date | None = None) -> list[dict]:
    """Vintages whose period has closed and which have no outcome yet."""
    horizon = _as_datetime(on or date.today())
    with session_factory() as session:
        query = select(ForecastVintageModel).where(
            ForecastVintageModel.outcome_value.is_(None),
            ForecastVintageModel.period <= horizon,
        )
        if identity_key:
            query = query.where(ForecastVintageModel.identity_key == identity_key)
        return [_row(item) for item in
                session.scalars(query.order_by(ForecastVintageModel.period))]


def settle(session_factory, identity_key: str, actuals: dict[date, float]) -> dict[str, Any]:
    """Append what actually happened to every vintage whose period is now known.

    Writes only the four outcome columns. A vintage that has already been settled is left alone -
    re-settling with a revised price would change a score that has already been reported, and a
    score that moves after the fact is worth nothing.
    """
    by_period = {_as_datetime(day): value for day, value in actuals.items()}
    settled = 0
    with session_factory() as session:
        rows = list(session.scalars(
            select(ForecastVintageModel).where(
                ForecastVintageModel.identity_key == identity_key,
                ForecastVintageModel.outcome_value.is_(None),
            )))
        for row in rows:
            period = row.period if row.period.tzinfo else row.period.replace(tzinfo=timezone.utc)
            truth = by_period.get(period)
            if truth is None:
                continue
            row.outcome_value = float(truth)
            row.outcome_recorded_at = datetime.now(timezone.utc)
            row.error = float(truth) - float(row.value)
            low = (row.quantiles or {}).get(LOW_QUANTILE)
            high = (row.quantiles or {}).get(HIGH_QUANTILE)
            # None, not False, when there was no band: a forecast that never made an interval did not
            # get one wrong, and counting it as a miss would understate every band that was offered.
            row.inside_band = (None if low is None or high is None
                               else bool(float(low) <= float(truth) <= float(high)))
            settled += 1
        session.commit()
    return {"status": "ok", "identity_key": identity_key, "settled": settled}


def performance(session_factory, identity_key: str = "", *,
                states: Sequence[str] = (RELEASED, CANDIDATE, CONTROL)) -> list[dict]:
    """What each model's recorded claims turned out to be worth, per horizon.

    Counts, never a probability. "Held 13 of 16" is a claim a reader can weigh; "about 80% of the
    time" is one they cannot, and it is the one that was wrong.
    """
    with session_factory() as session:
        query = select(ForecastVintageModel).where(
            ForecastVintageModel.outcome_value.is_not(None),
            ForecastVintageModel.state.in_(list(states)),
        )
        if identity_key:
            query = query.where(ForecastVintageModel.identity_key == identity_key)
        rows = list(session.scalars(query))

    buckets: dict[tuple[str, str, int], dict] = {}
    for row in rows:
        bucket = buckets.setdefault(
            (row.identity_key, row.model_key, row.horizon_weeks),
            {"identity_key": row.identity_key, "model": row.model_key,
             "horizon_weeks": row.horizon_weeks, "state": row.state,
             "settled": 0, "errors": [], "banded": 0, "inside": 0},
        )
        bucket["settled"] += 1
        bucket["errors"].append(abs(float(row.error or 0.0)))
        if row.inside_band is not None:
            bucket["banded"] += 1
            bucket["inside"] += int(row.inside_band)

    out = []
    for bucket in buckets.values():
        errors = bucket.pop("errors")
        bucket["mae"] = round(sum(errors) / len(errors), 2) if errors else None
        out.append(bucket)
    return sorted(out, key=lambda item: (item["identity_key"], item["horizon_weeks"], item["model"]))


def _row(model: ForecastVintageModel) -> dict:
    return {
        "vintage_key": model.vintage_key, "identity_key": model.identity_key,
        "model": model.model_key, "horizon_weeks": model.horizon_weeks,
        "as_of": model.as_of.date().isoformat(), "period": model.period.date().isoformat(),
        "value": model.value, "quantiles": model.quantiles or {},
        "calibration": model.calibration or {}, "state": model.state,
        "outcome_value": model.outcome_value, "error": model.error,
        "inside_band": model.inside_band,
    }


def history(session_factory, identity_key: str, *, model_key: str = "",
            horizon_weeks: int = 0, limit: int = 200) -> list[dict]:
    """Every claim made for one identity, newest as-of first. The audit trail."""
    with session_factory() as session:
        query = select(ForecastVintageModel).where(
            ForecastVintageModel.identity_key == identity_key)
        if model_key:
            query = query.where(ForecastVintageModel.model_key == model_key)
        if horizon_weeks:
            query = query.where(ForecastVintageModel.horizon_weeks == horizon_weeks)
        rows = session.scalars(
            query.order_by(ForecastVintageModel.as_of.desc(),
                           ForecastVintageModel.horizon_weeks).limit(limit))
        return [_row(row) for row in rows]
