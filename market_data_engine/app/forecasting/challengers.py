"""Challengers: scored in every review, never the live winner until an administrator approves them
(Adaptive Forecast Method Selection, WP4, Task 10).

- A Challenger takes part in every Model Fit Review and is ranked with the rest, but `selection.propose`
  only ever proposes a Production Eligible method, so a Challenger that scores first is REPORTED beside
  the winner (`challenger_ahead`) and never becomes it.
- `approve_challenger(method_key, actor, reason)` promotes it to Production Eligible, with a permanent
  approval record (who, why, when). Promotion changes what the method MAY do; it selects nothing. The
  method competes from the next review on, per series and horizon, like every other eligible method.
- `challenger_evidence(review_id)` lists, per series and horizon, the Challengers that out-scored the
  proposed winner in a review - the evidence an administrator promotes on.
"""
from __future__ import annotations

from typing import Any

from app.domain.enums import ForecastMethodStatus
from app.forecasting.domain import GovernanceError
from app.forecasting.governance import ForecastMethodRegistry, ModelFitReviewRepository


def approve_challenger(session_factory, method_key: str, *, actor: str, reason: str) -> dict[str, Any]:
    with session_factory() as session:
        registry = ForecastMethodRegistry(session)
        method = registry.get(method_key)
        if method is None:
            raise GovernanceError(f"no governed method {method_key!r}")
        if method.status is not ForecastMethodStatus.CHALLENGER:
            raise GovernanceError(f"{method_key} is {method.status.value}; only a Challenger is approved")
        approval = registry.promote(method_key, actor=actor, reason=reason)
        session.commit()
    return {"method_key": method_key, "status": ForecastMethodStatus.PRODUCTION_ELIGIBLE.value,
            "actor": approval.actor, "reason": approval.reason,
            "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
            "selections_changed": 0}


def challenger_evidence(session_factory, review_id: str | None = None) -> dict[str, Any]:
    """Per series and horizon, the Challengers ranked above the proposed winner in a review."""
    with session_factory() as session:
        reviews = ModelFitReviewRepository(session)
        if review_id:
            review = reviews.get(review_id)
        else:                                   # the last FULL review, never a partial look (fourth review)
            from app.forecasting.review import _latest_full_success
            row = _latest_full_success(session)
            review = reviews.get(row.id) if row is not None else None
        if review is None:
            return {"review_id": None, "pairs": []}
        registry = ForecastMethodRegistry(session)
        by_id = {m.id: m for m in registry.list_all()}
        results = reviews.results(review.id)
    pairs: dict[tuple[str, int], list] = {}
    for r in results:
        pairs.setdefault((r.identity_id, r.horizon_weeks), []).append(r)
    def status_then(r) -> str | None:
        """The method's status WHEN the review ran (recorded in its result), else today's."""
        recorded = (r.details or {}).get("method_status")
        if recorded:
            return recorded
        method = by_id.get(r.method_id)
        return method.status.value if method is not None else None

    out = []
    for (identity_id, weeks), rows in sorted(pairs.items()):
        ranked = sorted((r for r in rows if r.rank is not None), key=lambda r: r.rank)
        winner = next((r for r in ranked if by_id.get(r.method_id) is not None
                       and status_then(r) == ForecastMethodStatus.PRODUCTION_ELIGIBLE.value), None)
        ahead = [r for r in ranked if (winner is None or r.rank < winner.rank) and by_id.get(r.method_id) is not None
                 and status_then(r) == ForecastMethodStatus.CHALLENGER.value]
        if ahead:
            out.append({"identity_id": identity_id, "horizon_weeks": weeks,
                        "winner": by_id[winner.method_id].method_key if winner else None,
                        "winner_mae": winner.matched_mae if winner else None,
                        "challengers_ahead": [{"method_key": by_id[r.method_id].method_key, "rank": r.rank,
                                               "mae": r.matched_mae} for r in ahead]})
    return {"review_id": review.id, "pairs": out}
