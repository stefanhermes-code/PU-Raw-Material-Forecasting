"""Method Fit: why the method in force is in force, without reading logs or tables (WP5, Task 11).

`GET /model-fit?identity_key=...` answers per horizon, in one read:

- user view: the method in force, AUTOMATIC or MANUAL OVERRIDE, the last full review's date, that
  method's matched MAE and directional accuracy in it, and the next review date;
- administrator view: the review's automatic winner, the top alternatives (ranked, Challengers
  marked), the selection history, the override (who, why) and an OVERRIDE RECOMMENDATION when the
  override's method differs from the automatic winner, and the Challengers that out-scored the winner.

Read-only. Built from the governance tables the review and the overrides write.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db.models import ModelFitResultModel, ModelFitReviewModel
from app.domain.enums import ForecastMethodStatus
from app.forecasting.governance import (
    ForecastMethodRegistry, ForecastOverrideRepository, ForecastSelectionRepository,
)
from app.forecasting.production import identity_by_key, in_force
from app.domain.series_key import market_of
from app.forecasting.review import last_successful_review, review_state

TOP_ALTERNATIVES = 5


def model_fit(session_factory, identity_key: str, horizons: tuple[int, ...] = (4, 8, 12)) -> dict[str, Any]:
    schedule = review_state(session_factory)
    with session_factory() as session:
        identity = identity_by_key(session, identity_key)
        if identity is None:
            return {}
        registry = ForecastMethodRegistry(session)
        methods = {m.id: m for m in registry.list_all()}
        last = last_successful_review(session)
        out: dict[str, Any] = {
            "identity_key": identity_key,
            # Which market this series belongs to. The fit is recorded per series and the pages
            # offer per market, and where the two differ the reader should be able to see it.
            "market": " | ".join(market_of(identity)),
            "last_review": {"id": last.id, "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                            "cutoff": last.cutoff.date().isoformat() if last.cutoff else None} if last else None,
            # No `next_review_due`. `review_state` has returned no `due_at` since the cadence was
            # abolished on 29 October 2026, so this field was null on every answer and the two
            # screens reading it printed a dash.
            "review_state": schedule.get("state"),
            "automatic_selection_enabled": schedule.get("automatic_selection_enabled"),
            "horizons": {},
        }
        for weeks in horizons:
            chosen = in_force(session, identity.id, weeks)
            results = []
            if last is not None:
                results = list(session.scalars(select(ModelFitResultModel).where(
                    ModelFitResultModel.review_id == last.id, ModelFitResultModel.identity_id == identity.id,
                    ModelFitResultModel.horizon_weeks == int(weeks))))
            ranked = sorted((r for r in results if r.rank is not None), key=lambda r: r.rank)

            def row(r) -> dict[str, Any]:
                method = methods.get(r.method_id)
                status = (r.details or {}).get("method_status") or (method.status.value if method else None)
                return {"method_key": method.method_key if method else r.method_id, "rank": r.rank,
                        "matched_mae": r.matched_mae, "directional_accuracy": r.directional_accuracy,
                        "matched_dates": r.matched_dates, "status_at_review": status,
                        "challenger": status == ForecastMethodStatus.CHALLENGER.value}

            winner = next((r for r in ranked if row(r)["status_at_review"] == ForecastMethodStatus.PRODUCTION_ELIGIBLE.value
                           and methods.get(r.method_id) is not None and methods[r.method_id].selectable), None)
            active_key = (chosen.method.method_key if chosen and chosen.method else (chosen.method_id if chosen else None))
            active_result = next((r for r in results if chosen and r.method_id == (chosen.method.id if chosen.method else chosen.method_id)), None)
            override = ForecastOverrideRepository(session).get_active(identity.id, weeks)
            history = []
            for s in ForecastSelectionRepository(session).history(identity.id, weeks):
                m = methods.get(s.method_id)
                history.append({"method_key": m.method_key if m else s.method_id, "source": s.source.value,
                                "review_id": s.review_id, "selected_at": s.selected_at.isoformat() if s.selected_at else None,
                                "active": s.active})
            entry = {
                # user view
                "active_method": active_key,
                "selection": (chosen.source.value if chosen else "NOT_GOVERNED"),
                "matched_mae": active_result.matched_mae if active_result else None,
                "directional_accuracy": active_result.directional_accuracy if active_result else None,
                # administrator view
                "automatic_winner": methods[winner.method_id].method_key if winner else None,
                "top_alternatives": [row(r) for r in ranked[:TOP_ALTERNATIVES]],
                "challengers_ahead": [row(r) for r in ranked if winner is not None and r.rank < winner.rank
                                      and row(r)["challenger"]],
                "history": history,
                "override": ({"method_key": methods[override.method_id].method_key if override.method_id in methods
                              else override.method_id, "actor": override.actor, "reason": override.reason,
                              "since": override.created_at.isoformat() if override.created_at else None}
                             if override else None),
            }
            entry["override_recommendation"] = bool(override and winner and override.method_id != winner.method_id)
            out["horizons"][str(weeks)] = entry
        if last is not None:
            notes = {}
            try:
                notes = json.loads(session.get(ModelFitReviewModel, last.id).notes or "{}")
            except (TypeError, ValueError):
                pass
            out["last_review"]["summary"] = {k: notes.get(k) for k in ("series_ranked", "series_judged",
                                                                        "selections_applied", "partial")}
    return out


def win_summary(session_factory, review_id: str | None = None) -> dict[str, Any]:
    """Management view (Review Focus 5): per horizon, how often each method ranked first across the
    review's series - with a mirrored or duplicated history counted ONCE. Every catalogue series stays
    individually selectable; only this aggregate discounts the copies."""
    from collections import Counter

    with session_factory() as session:
        review = None
        if review_id:
            review = session.get(ModelFitReviewModel, review_id)
        else:
            review = last_successful_review(session)
        if review is None:
            return {"review_id": None, "horizons": {}}
        methods = {m.id: m.method_key for m in ForecastMethodRegistry(session).list_all()}
        rows = list(session.scalars(select(ModelFitResultModel).where(
            ModelFitResultModel.review_id == review.id, ModelFitResultModel.rank == 1)))
    out: dict[str, Any] = {"review_id": review.id, "horizons": {}}
    for weeks in sorted({r.horizon_weeks for r in rows}):
        firsts = [r for r in rows if r.horizon_weeks == weeks]
        counted = [r for r in firsts if not (r.details or {}).get("duplicate_of")]
        out["horizons"][str(weeks)] = {
            "series": len(counted), "duplicates_not_counted": len(firsts) - len(counted),
            "first_place": dict(Counter(methods.get(r.method_id, r.method_id) for r in counted).most_common())}
    return out


def accuracy_in_force(session_factory) -> dict[str, Any]:
    """How accurate the methods IN FORCE actually are, by chemical and by horizon.

    Stefan, 23 October 2026, on being given these numbers by hand: "that is also what the
    application should present." The Forecast Performance page showed the naive and
    moving-average baselines - the controls - and never the methods that are actually serving
    forecasts.

    A method's error is recorded as `matched_mae`, which is ABSOLUTE and in the market's own
    currency, so it cannot be compared across markets or averaged. It is turned into a share of
    that market's own price level here, which is currency-neutral, and the level is the mean
    observed price over the last two years. Accuracy is 100 minus that share, the same convention
    the comparison workbooks use.

    Direction is carried beside it on purpose. A method can sit within 8% of the price and call
    the up-or-down move right barely more than half the time, because most months move very little:
    the level is easy and the sign is close to a coin toss. Showing one without the other flatters.
    """
    from sqlalchemy import text as _text

    rows_sql = _text("""
        SELECT i.product_family AS chemical, f.horizon_weeks AS weeks,
               f.matched_mae AS mae, f.directional_accuracy AS direction,
               (SELECT avg(p.price) FROM price_observation p
                 WHERE p.identity_id = s.identity_id
                   AND p.observation_date >= :since) AS level
        FROM forecast_method_selection s
        JOIN model_fit_result f
          ON f.identity_id = s.identity_id
         AND f.horizon_weeks = s.horizon_weeks
         AND f.method_id = s.method_id
        JOIN market_identity i ON i.id = s.identity_id
        WHERE s.active = true AND f.matched_mae IS NOT NULL
    """)
    # Bound, not `CURRENT_DATE - INTERVAL '2 years'`: that is PostgreSQL's spelling and the store
    # is PostgreSQL, but every test runs on SQLite, where it is a syntax error. A date computed here
    # is the same answer on both.
    since = datetime.now(timezone.utc).date() - timedelta(days=730)
    by_chemical: dict[str, list] = {}
    by_horizon: dict[int, list] = {}
    every: list = []
    with session_factory() as session:
        for row in session.execute(rows_sql, {"since": since}).mappings():
            level = row["level"]
            if not level or float(level) <= 0:
                continue
            share = float(row["mae"]) / float(level) * 100.0
            direction = None if row["direction"] is None else float(row["direction"]) * 100.0
            entry = (share, direction)
            by_chemical.setdefault(str(row["chemical"] or "UNKNOWN"), []).append(entry)
            by_horizon.setdefault(int(row["weeks"]), []).append(entry)
            every.append(entry)

    def fold(entries: list) -> dict[str, Any]:
        if not entries:
            return {}
        errors = [e for e, _d in entries]
        directions = [d for _e, d in entries if d is not None]
        return {"accuracy": round(100.0 - sum(errors) / len(errors), 1),
                "error": round(sum(errors) / len(errors), 1),
                "direction": round(sum(directions) / len(directions), 1) if directions else None,
                "selections": len(entries)}

    return {
        "overall": fold(every),
        "by_chemical": {name: fold(rows) for name, rows in sorted(by_chemical.items())},
        "by_horizon": {str(weeks): fold(rows) for weeks, rows in sorted(by_horizon.items())},
    }


def forecasts_available(session_factory) -> list[dict[str, Any]]:
    """Every market that has a forecast method in force, one row each.

    Stefan, 30 October 2026: *"a very simple table showing which forecasts are now available,
    downloadable as csv, not too complicated."*

    A market holds one selection per horizon and they are rarely the same method - adipic acid
    China takes `momentum` at four weeks and nothing at all at eight - so the methods come back
    keyed by horizon rather than as one value, and a horizon the market has no method for is simply
    absent. That absence is the useful part: it is the difference between "we forecast this market
    at three horizons" and "at one".

    `chosen_at` is the OLDEST of the market's choices, for the same reason the watch uses the
    oldest: a market is only as current as the choice that has seen least.
    """
    from sqlalchemy import text as _text

    sql = _text("""
        SELECT i.product_family AS chemical, i.market_geography AS geography,
               i.commercial_basis AS basis, i.observation_type AS price_kind,
               i.packaging AS packaging, i.unit AS unit,
               s.horizon_weeks AS weeks, m.method_key AS method,
               s.selected_at AS chosen_at, s.source AS chosen_by
        FROM forecast_method_selection s
        JOIN market_identity i ON i.id = s.identity_id
        JOIN forecast_method m ON m.id = s.method_id
        WHERE s.active = true
    """)
    markets: dict[tuple, dict[str, Any]] = {}
    with session_factory() as session:
        for row in session.execute(sql).mappings():
            key = (str(row["chemical"] or ""), str(row["geography"] or ""),
                   str(row["basis"] or ""), str(row["price_kind"] or ""),
                   str(row["packaging"] or ""))
            market = markets.setdefault(key, {
                "chemical": row["chemical"], "geography": row["geography"],
                "basis": row["basis"], "price_kind": row["price_kind"],
                "packaging": row["packaging"], "unit": row["unit"],
                "methods": {}, "chosen_at": None, "chosen_by": row["chosen_by"],
            })
            market["methods"][str(int(row["weeks"]))] = row["method"]
            when = row["chosen_at"]
            if when is not None and (market["chosen_at"] is None or when < market["chosen_at"]):
                market["chosen_at"] = when

    out = []
    for market in markets.values():
        when = market["chosen_at"]
        out.append({**market,
                    "horizons": sorted(int(weeks) for weeks in market["methods"]),
                    "chosen_at": when.isoformat() if hasattr(when, "isoformat") else when})
    return sorted(out, key=lambda row: (str(row["chemical"] or ""), str(row["geography"] or ""),
                                        str(row["basis"] or "")))


def with_last_nights(session_factory, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The table of what is in force, with what the last few nights actually produced (v1.2.137).

    The table said which method is IN FORCE and nothing about whether it answered. On
    26 November 2026 seven markets had a method in force that refused every night, and the table
    showed all seven as forecast. So the newest run for each market and horizon is read beside it:
    `standing_in` names the method that answered where the one in force could not, and `refused`
    carries the reason where nobody answered. Four days back is one more than a stored answer
    stays current for, and only five plain columns are read - never a run's inputs.

    Kept apart from `forecasts_available`, which is the selections and nothing else.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select as _select

    from app.db.models import GovernedForecastRunModel, MarketIdentityModel

    since = datetime.now(timezone.utc) - timedelta(days=4)
    newest: dict[tuple, tuple] = {}
    with session_factory() as session:
        recent = session.execute(_select(
            GovernedForecastRunModel.identity_id, GovernedForecastRunModel.horizon_weeks,
            GovernedForecastRunModel.status, GovernedForecastRunModel.method_key,
            GovernedForecastRunModel.reason).where(GovernedForecastRunModel.created_at >= since)
            .order_by(GovernedForecastRunModel.created_at.desc())).all()
        market_of = {identity.id: (
            str(identity.product_family or ""), str(identity.market_geography or ""),
            str(identity.commercial_basis or ""), str(identity.observation_type or ""),
            str(identity.packaging or "")) for identity in session.scalars(_select(MarketIdentityModel))}
    for identity_id, weeks, status, method, reason in recent:
        newest.setdefault((market_of.get(identity_id), int(weeks)), (status, method, reason or ""))
    out = []
    for row in rows:
        key = (str(row.get("chemical") or ""), str(row.get("geography") or ""),
               str(row.get("basis") or ""), str(row.get("price_kind") or ""),
               str(row.get("packaging") or ""))
        standing_in, refused = {}, {}
        for weeks, in_force in (row.get("methods") or {}).items():
            status, method, reason = newest.get((key, int(weeks)), ("", "", ""))
            if status == "SUCCEEDED" and method != in_force and reason.startswith("standing in for "):
                standing_in[str(weeks)] = method
            elif status and status != "SUCCEEDED" and method == in_force:
                refused[str(weeks)] = reason
        out.append({**row, "standing_in": standing_in, "refused": refused})
    return out


def make_router(session_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/model-fit")
    def get_model_fit(identity_key: str, horizons: str = "4,8,12"):
        try:
            weeks = tuple(int(part) for part in horizons.split(",") if part.strip()) or (4, 8, 12)
        except ValueError:
            raise HTTPException(status_code=400, detail="horizons must be whole weeks, comma separated")
        found = model_fit(session_factory, identity_key, weeks)
        if not found:
            raise HTTPException(status_code=404, detail="identity not found")
        return found

    @router.get("/model-fit/summary")
    def get_summary(review_id: str = ""):
        return win_summary(session_factory, review_id or None)

    @router.get("/model-fit/accuracy")
    def get_accuracy():
        return accuracy_in_force(session_factory)

    @router.get("/forecasts-available")
    def get_forecasts_available():
        return with_last_nights(session_factory, forecasts_available(session_factory))

    return router
