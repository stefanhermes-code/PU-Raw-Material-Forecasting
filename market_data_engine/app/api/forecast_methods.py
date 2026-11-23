"""Administrator endpoints for governed forecast methods (Adaptive Forecast Method Selection, WP3, Task 8).

- `GET  /governed-forecast?identity_key=...`        what is in force per horizon and its latest run
- `GET  /forecast-methods/overrides`                every override in force
- `POST /forecast-methods/overrides`                set one (series, horizon) override
- `POST /forecast-methods/overrides/remove`         remove it
- `GET  /forecast-methods/review-status`            next review due, missed reviews (WP4)
- `GET/POST /forecast-methods/settings`             automatic selection switch, review cadence (WP4)
- `GET  /forecast-methods/challenger-evidence`      Challengers that out-scored the winner (WP4)
- `POST /forecast-methods/challengers/approve`      promote a Challenger, recorded (WP4)

A refused override answers 409 with the reason in words (not Production Eligible, not executable for
that series and horizon now, or no reason/actor given). Nothing here runs on the forecast page path:
setting an override asks the method once, to prove it can answer.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.forecasting.domain import GovernanceError


class OverrideRequest(BaseModel):
    identity_key: str
    horizon_weeks: int = Field(gt=0)
    method_key: str
    actor: str
    reason: str


class SettingChange(BaseModel):
    key: str
    value: bool | int
    actor: str
    reason: str


class ChallengerApprovalRequest(BaseModel):
    method_key: str
    actor: str
    reason: str


class OverrideRemoval(BaseModel):
    identity_key: str
    horizon_weeks: int = Field(gt=0)
    actor: str
    reason: str


def make_router(session_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/governed-forecast")
    def governed_forecast(identity_key: str, horizons: str = "4,8,12"):
        from app.forecasting.production import latest_governed
        try:
            weeks = tuple(int(part) for part in horizons.split(",") if part.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="horizons must be whole weeks, comma separated")
        return latest_governed(session_factory, identity_key, weeks or (4, 8, 12))

    @router.get("/forecast-methods/overrides")
    def overrides_in_force():
        from sqlalchemy import select

        from app.db.models import MarketIdentityModel
        from app.forecasting.governance import ForecastMethodRegistry, ForecastOverrideRepository
        from app.domain.series_key import market_of
        from app.forecasting.review_job import identity_key_of
        with session_factory() as session:
            rows = list(session.scalars(select(MarketIdentityModel)))
            keys = {row.id: identity_key_of(row) for row in rows}
            markets = {row.id: " | ".join(market_of(row)) for row in rows}
            registry = ForecastMethodRegistry(session)
            out = []
            for o in ForecastOverrideRepository(session).list_active():
                method = registry.get_by_id(o.method_id)
                out.append({"identity_key": keys.get(o.identity_id, o.identity_id),
                            "market": markets.get(o.identity_id, ""), "horizon_weeks": o.horizon_weeks,
                            "method_key": method.method_key if method else o.method_id, "actor": o.actor,
                            "reason": o.reason, "created_at": o.created_at.isoformat() if o.created_at else None})
            return sorted(out, key=lambda r: (r["identity_key"], r["horizon_weeks"]))

    @router.post("/forecast-methods/overrides")
    def set_override(request: OverrideRequest):
        from app.forecasting.overrides import set_override as set_it
        try:
            return set_it(session_factory, request.identity_key, request.horizon_weeks, request.method_key,
                          actor=request.actor, reason=request.reason)
        except GovernanceError as refused:
            raise HTTPException(status_code=409, detail=str(refused))

    @router.post("/forecast-methods/overrides/remove")
    def remove_override(request: OverrideRemoval):
        from app.forecasting.overrides import remove_override as remove_it
        try:
            return remove_it(session_factory, request.identity_key, request.horizon_weeks,
                             actor=request.actor, reason=request.reason)
        except GovernanceError as refused:
            raise HTTPException(status_code=409, detail=str(refused))

    # ---- WP4: review schedule, governance settings, challengers

    @router.get("/forecast-methods/review-status")
    def review_status():
        # Was the quarterly schedule - cadence, due date, overdue days. There is no calendar now:
        # a method is re-checked when a reading arrives that its choice has not seen.
        from app.forecasting.review import review_state
        return review_state(session_factory)

    @router.get("/forecast-methods/settings")
    def settings():
        from app.forecasting.settings import DEFAULTS, get_setting
        with session_factory() as session:
            return {key: get_setting(session, key) for key in DEFAULTS}

    @router.post("/forecast-methods/settings")
    def change_setting(request: SettingChange):
        from app.forecasting.settings import set_setting
        try:
            with session_factory() as session:
                out = set_setting(session, request.key, request.value, actor=request.actor, reason=request.reason)
                session.commit()
                return out
        except GovernanceError as refused:
            raise HTTPException(status_code=409, detail=str(refused))

    @router.get("/forecast-methods/challenger-evidence")
    def evidence(review_id: str = ""):
        from app.forecasting.challengers import challenger_evidence
        return challenger_evidence(session_factory, review_id or None)

    @router.post("/forecast-methods/challengers/approve")
    def approve(request: ChallengerApprovalRequest):
        from app.forecasting.challengers import approve_challenger
        try:
            return approve_challenger(session_factory, request.method_key, actor=request.actor, reason=request.reason)
        except GovernanceError as refused:
            raise HTTPException(status_code=409, detail=str(refused))

    return router
