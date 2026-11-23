"""Release checks against the store itself (Adaptive Forecast Method Selection, WP5 Task 12; decision D9).

The plan's "Final release checks" and the store-checkable Global Constraints, run against whatever store
`DATABASE_URL` points at - the live Supabase store included - not against fixtures. Each check returns
PASS or FAIL with the evidence in words. `python -m app.cli validate` prints them; activation
(Switch-Automatic-Selection.bat) should only follow an all-PASS run plus a successful full review.

These are invariants of the data. Behaviour (atomicity, leakage, matched dates...) is proven by the test
suite; the criteria matrix (docs/validation/acceptance_criteria_matrix.md) maps every requirement to one
or the other.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.models import (
    ForecastMethodModel, ForecastMethodOverrideModel, ForecastMethodSelectionModel, ForecastVintageModel,
    GovernedForecastRunModel, ModelFitResultModel, ModelFitReviewModel,
)
from app.domain.enums import ForecastMethodStatus, ReviewStatus
from app.forecasting.domain import FLAT_CONTROL_KEYS
from app.forecasting.settings import get_setting


@dataclass(frozen=True)
class Check:
    code: str
    requirement: str
    passed: bool
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "requirement": self.requirement,
                "result": "PASS" if self.passed else "FAIL", "evidence": self.evidence}


def _notes(row) -> dict:
    try:
        return json.loads(row.notes or "{}") or {}
    except (TypeError, ValueError):
        return {}


def run_checks(session_factory, *, for_activation: bool = False) -> list[Check]:
    """Every store check. `for_activation` adds the one that only matters before switching on:
    a successful FULL review exists."""
    out: list[Check] = []
    with session_factory() as session:
        methods = {m.id: m for m in session.scalars(select(ForecastMethodModel))}
        by_key = {m.method_key: m for m in methods.values()}
        selections = list(session.scalars(select(ForecastMethodSelectionModel).where(
            ForecastMethodSelectionModel.active.is_(True))))
        overrides = list(session.scalars(select(ForecastMethodOverrideModel)))
        reviews = {r.id: r for r in session.scalars(select(ModelFitReviewModel))}
        governed_vintages = list(session.scalars(select(ForecastVintageModel).where(
            ForecastVintageModel.selection_source.is_not(None))))
        runs = list(session.scalars(select(GovernedForecastRunModel)))
        enabled = get_setting(session, "automatic_selection_enabled")

        def add(code: str, requirement: str, problems: list[str], ok_text: str) -> None:
            out.append(Check(code, requirement, not problems,
                             ok_text if not problems else "; ".join(problems[:10]) +
                             (f" (+{len(problems) - 10} more)" if len(problems) > 10 else "")))

        # R1 - the registry: flat controls never selectable / Production Eligible
        flat = [m.method_key for m in methods.values()
                if (m.is_flat_control or m.method_key in FLAT_CONTROL_KEYS)
                and m.status == ForecastMethodStatus.PRODUCTION_ELIGIBLE.value]
        missing = sorted(FLAT_CONTROL_KEYS - set(by_key))
        add("R1", "No unchanged-price control is Production Eligible or selectable",
            [f"{k} is PRODUCTION_ELIGIBLE" for k in flat] + [f"control {k} not registered" for k in missing],
            f"{len(methods)} methods registered; controls {sorted(FLAT_CONTROL_KEYS)} are not eligible")

        # R2 - one active selection per pair, resting on a successful FULL review, Production Eligible
        pairs = Counter((s.identity_id, s.horizon_weeks) for s in selections)
        problems = [f"{p} has {n} active selections" for p, n in pairs.items() if n > 1]
        for s in selections:
            review = reviews.get(s.review_id or "")
            method = methods.get(s.method_id)
            if s.source == "AUTOMATIC" and (review is None or review.status != ReviewStatus.SUCCEEDED.value
                                            or _notes(review).get("partial")):
                problems.append(f"selection {s.id} ({s.identity_id} {s.horizon_weeks}w) does not rest on a "
                                f"successful full review")
            if method is None or method.method_key in FLAT_CONTROL_KEYS:
                problems.append(f"selection {s.id} names an unknown or flat-control method")
        add("R2", "Every active selection has one series, one horizon and one supporting successful review",
            problems, f"{len(selections)} active selections, each resting on a successful full review")

        # R3 - overrides carry actor, timestamp, reason; removals carry who and why
        problems = []
        for o in overrides:
            if not (o.actor or "").strip() or not (o.reason or "").strip() or o.created_at is None:
                problems.append(f"override {o.id} lacks actor, reason or time")
            if o.removed_at is not None and (not (o.removed_by or "").strip() or not (o.removal_reason or "").strip()):
                problems.append(f"override {o.id} removed without who or why")
            if o.method_id in methods and methods[o.method_id].method_key in FLAT_CONTROL_KEYS:
                problems.append(f"override {o.id} names a flat control")
        add("R3", "Every manual override has actor, timestamp and reason", problems,
            f"{len(overrides)} overrides on record, all with actor, time and reason")

        # R4 - governed vintages carry method, version, source (and review for AUTOMATIC)
        problems = []
        for v in governed_vintages:
            if not v.selected_method_id or not v.model_version or not v.selection_source:
                problems.append(f"vintage {v.vintage_key} lacks method id, version or source")
            if v.selection_source == "AUTOMATIC" and not v.review_id:
                problems.append(f"vintage {v.vintage_key} is AUTOMATIC without a review")
            if not (v.metadata_json or {}).get("cutoff"):
                problems.append(f"vintage {v.vintage_key} lacks its cutoff")
        add("R4", "Every production forecast vintage records method/version and selection source", problems,
            f"{len(governed_vintages)} governed vintages, all with method, version, source and cutoff")

        # R5 - no governed vintage written for a FAILED run (no hidden fallback)
        problems = [f"FAILED run {r.run_key} points at vintage {r.vintage_id}" for r in runs
                    if r.status == "FAILED" and r.vintage_id]
        add("R5", "A failed method is recorded as failed; no hidden fallback carries its name", problems,
            f"{sum(1 for r in runs if r.status == 'FAILED')} failed runs on record, none with a forecast attached")

        # R6 is gone, and the code is not reused. The quarterly cadence it checked was abolished on
        # 29 October 2026: `review_cadence_months` left `settings.DEFAULTS`, and this line went on
        # asking `get_setting` for it, which raises for a key it does not know. So `run_checks`
        # raised on its third line and no check ran at all - including R9, the activation guard,
        # which then refused ON with "unknown governance setting" rather than with the check a
        # reader could act on. A method is re-checked when a reading arrives its choice has not
        # seen (`raw_material_forecasting.method_watch`), and that is watched continuously rather
        # than asserted once a quarter, so there is no cadence left to check.

        # R7 - every review closed once; no RUNNING older than the abandon window left open is a warning-level FAIL
        stale = [r.id for r in reviews.values() if r.status == ReviewStatus.RUNNING.value and r.finished_at is not None]
        add("R7", "Every Model Fit Review is closed once (RUNNING, SUCCEEDED or FAILED; never re-opened)",
            [f"review {i} is RUNNING with a finish time" for i in stale],
            f"{len(reviews)} reviews on record")

        # R8 - results of successful reviews name known methods and carry the examined frequency
        result_rows = list(session.scalars(select(ModelFitResultModel)))
        problems = [f"result {r.id} names an unknown method" for r in result_rows if r.method_id not in methods]
        problems += [f"result {r.id} lacks its examined frequency" for r in result_rows
                     if reviews.get(r.review_id) is not None and not (r.details or {}).get("frequency")]
        add("R8", "Model Fit Results are permanent records with method and examined frequency", problems,
            f"{len(result_rows)} results on record")

        # R9 - automatic selection is not on without a successful full review (the activation guard)
        full = [r for r in reviews.values() if r.status == ReviewStatus.SUCCEEDED.value and not _notes(r).get("partial")]
        if for_activation or enabled:
            add("R9", "Initial application-native Model Fit Review is successful before feature activation",
                [] if full else ["no successful full Model Fit Review on record"],
                f"{len(full)} successful full review(s); latest {max((r.finished_at for r in full), default=None)}")
        # R10 - Challengers are only promoted with an approval record
        from app.db.models import ChallengerApprovalModel
        approvals = {a.method_id for a in session.scalars(select(ChallengerApprovalModel))}
        seeded_eligible = {"momentum", "weighted_error"}
        problems = [f"{m.method_key} is Production Eligible without an approval record" for m in methods.values()
                    if m.status == ForecastMethodStatus.PRODUCTION_ELIGIBLE.value
                    and m.method_key not in seeded_eligible and m.id not in approvals]
        add("R10", "Challenger promotion requires explicit administrator action", problems,
            f"{len(approvals)} approval record(s); every promoted method has one")
    return out


def summary_lines(checks: list[Check]) -> list[str]:
    lines = [f"{c.code:4s} {'PASS' if c.passed else 'FAIL'}  {c.requirement}\n       {c.evidence}" for c in checks]
    failed = [c for c in checks if not c.passed]
    lines.append(f"\n{len(checks) - len(failed)} of {len(checks)} release checks pass."
                 + (" Activation must wait." if failed else ""))
    return lines
