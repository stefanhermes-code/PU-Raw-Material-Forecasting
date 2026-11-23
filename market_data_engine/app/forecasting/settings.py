"""Governance settings (WP4, decision D7): read with defaults, changed only with actor and reason.

- `automatic_selection_enabled` (default False): whether a review may commit its own selections.
  Off until the application-native validation (WP5) has passed. Stefan switched it on 21 October
  2026, so the watch may act on what it finds.
- `review_origins` (default 36): how many origins deep a review scores each market.

`review_cadence_months` was here, default 3, and it is gone. It said how long to wait before the
next quarterly review, and nothing ran one - measured 29 October 2026, the next was due 20 January
2027 and exactly one review had ever happened, because the only thing that could start one was a
batch file somebody had to double-click. Stefan, the same day: "No outside batch files, not historic
reviews, just get rid of these." A method is re-checked when a reading arrives that its choice has
not seen, which `raw_material_forecasting.method_watch` decides and the application runs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import ForecastGovernanceSettingModel
from app.forecasting.domain import GovernanceError

DEFAULTS: dict[str, Any] = {"automatic_selection_enabled": False, # D10: a full review examines each market's newest 36 origins (three years of
                            # monthly prints, the research's 36-60 expanding origins) - hours, not days.
                            "review_origins": 36}


def get_setting(session, key: str) -> Any:
    if key not in DEFAULTS:
        raise GovernanceError(f"unknown governance setting {key!r}")
    row = session.get(ForecastGovernanceSettingModel, key)
    return DEFAULTS[key] if row is None else (row.value or {}).get("value", DEFAULTS[key])


def set_setting(session, key: str, value: Any, *, actor: str, reason: str) -> dict[str, Any]:
    if key not in DEFAULTS:
        raise GovernanceError(f"unknown governance setting {key!r}")
    if not str(actor or "").strip() or not str(reason or "").strip():
        raise GovernanceError("a governance setting is changed with who and why")
    if key == "automatic_selection_enabled" and not isinstance(value, bool):
        raise GovernanceError("automatic_selection_enabled is true or false")
    if key == "review_origins" and (not isinstance(value, int) or isinstance(value, bool) or not 12 <= value <= 200):
        raise GovernanceError("review_origins is a whole number of origins, 12 to 200")
    now = datetime.now(timezone.utc)
    row = session.get(ForecastGovernanceSettingModel, key)
    if row is None:
        row = ForecastGovernanceSettingModel(key=key, value={"value": value}, actor=actor.strip(), reason=reason.strip(),
                                             history=[], updated_at=now)
        session.add(row)
    else:
        row.history = [*(row.history or []), {"value": (row.value or {}).get("value"), "actor": row.actor,
                                              "reason": row.reason, "until": now.isoformat()}]
        row.value = {"value": value}
        row.actor, row.reason, row.updated_at = actor.strip(), reason.strip(), now
    session.flush()
    return {"key": key, "value": value, "actor": row.actor, "reason": row.reason, "updated_at": now.isoformat()}
