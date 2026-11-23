"""Administrator overrides (Adaptive Forecast Method Selection, WP3, Task 8).

`set_override(identity_key, horizon, method_key, actor, reason)` puts one method in force for ONE series
and ONE horizon, above the automatic selection, until an administrator removes it. It is never replaced
automatically (CR). Two conditions, checked before anything is written:

- the method is PRODUCTION ELIGIBLE - a Challenger has to be promoted first, and a flat control never
  can be (the governance domain refuses it);
- the method is EXECUTABLE for that series and horizon now: it is asked once, at today's cutoff, with
  the production recipe (`production.ask`) and must answer that horizon. A method that is INELIGIBLE
  or FAILS here is refused with its reason. A method whose runtime is not installed where this runs
  (a foundation model asked from the interactive engine) is UNAVAILABLE here and is refused with that
  reason - the override is then set from the review environment, where the production job runs it.

`remove_override` closes the override with who and why; the automatic selection applies again from the
next production run. Other series and horizons are untouched by either (horizon isolation).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from app.domain.enums import ForecastMethodStatus
from app.forecasting.adapters.base import ForecastModelAdapter
from app.forecasting.adapters.catalogue import governed_adapters
from app.forecasting.domain import CONDITIONAL, ForecastMethodOverride, GovernanceError
from app.forecasting.governance import ForecastMethodRegistry, ForecastOverrideRepository
from app.forecasting.production import all_rows, ask, identity_by_key, prepare
from app.forecasting.review_job import feature_rows as load_feature_rows
from app.forecasting.store_series import forecast_rows


def check_executable(session_factory, identity, method_key: str, horizon_weeks: int, *,
                     adapters: Mapping[str, ForecastModelAdapter] | None = None,
                     issue_date: date | None = None) -> tuple[bool, str]:
    """(True, "") when the method answers this horizon for this series today; else (False, why)."""
    issue_date = issue_date or datetime.now(timezone.utc).date()
    adapters = adapters if adapters is not None else governed_adapters()
    adapter = adapters.get(method_key)
    if adapter is None:
        return False, f"no runnable adapter for {method_key}"
    rows = forecast_rows(session_factory, identity.id, all_versions=True)
    prepared = prepare(identity, rows, issue_date)
    if prepared is None:
        return False, "the series has no visible history today"
    with session_factory() as session:
        method = ForecastMethodRegistry(session).get(method_key)
    related = features = None
    if method is not None and method.kind == CONDITIONAL:
        related = all_rows(session_factory)
        features = load_feature_rows(session_factory, prepared.chemical, until=issue_date)
    output, _inputs = ask(adapter, prepared, horizon_weeks, related_rows=related, feature_rows=features)
    if output.succeeded and output.at(horizon_weeks) is not None:
        return True, ""
    return False, f"{output.status.value}: {output.reason or 'no answer for this horizon'}"


def set_override(session_factory, identity_key: str, horizon_weeks: int, method_key: str, *, actor: str,
                 reason: str, adapters: Mapping[str, ForecastModelAdapter] | None = None,
                 issue_date: date | None = None) -> dict[str, Any]:
    if not str(actor or "").strip() or not str(reason or "").strip():
        raise GovernanceError("an override carries who made it and why")
    if int(horizon_weeks) <= 0:
        raise GovernanceError("an override needs a horizon")
    with session_factory() as session:
        identity = identity_by_key(session, identity_key)
        if identity is None:
            raise GovernanceError(f"no market {identity_key!r}")
        method = ForecastMethodRegistry(session).get(method_key)
        if method is None:
            raise GovernanceError(f"no governed method {method_key!r}")
        if method.status is not ForecastMethodStatus.PRODUCTION_ELIGIBLE or not method.selectable:
            raise GovernanceError(f"{method_key} is {method.status.value}; only a Production Eligible method can "
                                  f"be put in force by an override")
    ok, why = check_executable(session_factory, identity, method_key, int(horizon_weeks),
                               adapters=adapters, issue_date=issue_date)
    if not ok:
        raise GovernanceError(f"{method_key} cannot forecast {identity_key} at {horizon_weeks} weeks now - {why}")
    override = ForecastMethodOverride(identity_id=identity.id, horizon_weeks=int(horizon_weeks), method_id=method.id,
                                      actor=actor.strip(), reason=reason.strip(),
                                      created_at=datetime.now(timezone.utc))
    with session_factory() as session:
        ForecastOverrideRepository(session).set(override)
        session.commit()
    return {"identity_key": identity_key, "horizon_weeks": int(horizon_weeks), "method_key": method_key,
            "actor": override.actor, "reason": override.reason, "created_at": override.created_at.isoformat(),
            "status": "IN_FORCE"}


def remove_override(session_factory, identity_key: str, horizon_weeks: int, *, actor: str,
                    reason: str) -> dict[str, Any]:
    with session_factory() as session:
        identity = identity_by_key(session, identity_key)
        if identity is None:
            raise GovernanceError(f"no market {identity_key!r}")
        removed = ForecastOverrideRepository(session).remove(identity.id, int(horizon_weeks),
                                                             actor=actor, reason=reason)
        session.commit()
    return {"identity_key": identity_key, "horizon_weeks": int(horizon_weeks),
            "status": "REMOVED" if removed else "NO_OVERRIDE_IN_FORCE"}
