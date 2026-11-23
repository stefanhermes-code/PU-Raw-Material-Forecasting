"""The method registry and its repositories: what the store knows about forecasting methods.

Adaptive Forecast Method Selection CR, Workpackage 1 (October 2026). The existing `ModelRegistry`
(registry.py) knows which model CODE exists and what its licence allows; this module keeps the
GOVERNANCE of methods in the store: status, history, selections, overrides, promotions, reviews.
The two meet in `seed_methods`, which registers every model the engine already runs, and the
research configurations of the CR as Challengers, so the first Model Fit Review has something to
score. Nothing here changes a live forecast.

Rules enforced here, not in callers:
- a flat control is never Production Eligible (domain.py refuses it; the registry refuses it too);
- a Challenger becomes Production Eligible only through `promote`, which records who and why;
- every status change is appended to the method's history with actor and reason;
- the seed is idempotent: rerunning it creates nothing twice and never demotes a method an
  administrator promoted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.db.models import (
    ChallengerApprovalModel, ForecastMethodModel, ForecastMethodOverrideModel,
    ForecastMethodSelectionModel, ModelFitResultModel, ModelFitReviewModel,
)
from app.domain.enums import ForecastMethodStatus, ReviewStatus, SelectionSource
from app.forecasting.domain import (
    CONDITIONAL, CONTROL, FLAT_CONTROL_KEYS, UNIVERSAL, ChallengerApproval, ForecastMethod,
    ForecastMethodOverride, ForecastMethodSelection, GovernanceError, ModelFitResult,
    ModelFitReview,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------- the registry

def _to_domain(row: ForecastMethodModel) -> ForecastMethod:
    return ForecastMethod(
        method_key=row.method_key, name=row.name, kind=row.kind,
        status=ForecastMethodStatus(row.status), version=row.version,
        is_flat_control=bool(row.is_flat_control), commercial_use=row.commercial_use,
        config=dict(row.config or {}), notes=row.notes or "", id=row.id)


class ForecastMethodRegistry:
    """The methods the application may run, and what each is allowed to do."""

    def __init__(self, session) -> None:
        self.session = session

    def add(self, method: ForecastMethod, *, actor: str = "SYSTEM", reason: str = "") -> ForecastMethod:
        if self.session.scalar(select(ForecastMethodModel).where(
                ForecastMethodModel.method_key == method.method_key)) is not None:
            raise GovernanceError(f"a method is already registered under {method.method_key!r}")
        now = _now()
        self.session.add(ForecastMethodModel(
            id=method.id, method_key=method.method_key, name=method.name, kind=method.kind,
            status=method.status.value, version=method.version,
            is_flat_control=method.is_flat_control, commercial_use=method.commercial_use,
            config=dict(method.config), notes=method.notes,
            status_history=[{"at": now.isoformat(), "from": None, "to": method.status.value,
                             "actor": actor, "reason": reason or "registered"}],
            created_at=now, updated_at=now))
        self.session.flush()
        return method

    def get(self, method_key: str) -> ForecastMethod | None:
        row = self.session.scalar(select(ForecastMethodModel).where(
            ForecastMethodModel.method_key == method_key))
        return _to_domain(row) if row else None

    def get_by_id(self, method_id: str) -> ForecastMethod | None:
        row = self.session.get(ForecastMethodModel, method_id)
        return _to_domain(row) if row else None

    def list_all(self) -> list[ForecastMethod]:
        rows = self.session.scalars(select(ForecastMethodModel).order_by(ForecastMethodModel.method_key))
        return [_to_domain(row) for row in rows]

    def list_by_status(self, status: ForecastMethodStatus) -> list[ForecastMethod]:
        rows = self.session.scalars(select(ForecastMethodModel).where(
            ForecastMethodModel.status == status.value).order_by(ForecastMethodModel.method_key))
        return [_to_domain(row) for row in rows]

    def selectable(self) -> list[ForecastMethod]:
        """Production Eligible and not a flat control: the only methods that may win or be chosen."""
        return [m for m in self.list_by_status(ForecastMethodStatus.PRODUCTION_ELIGIBLE)
                if not m.is_flat_control]

    def set_status(self, method_key: str, status: ForecastMethodStatus, *, actor: str,
                   reason: str) -> ForecastMethod:
        """Change what a method may do, with who and why written down.

        Promotion to Production Eligible goes through `promote`, which also writes the approval
        record; this refuses it so the audit trail cannot be skipped.
        """
        if not actor.strip() or not reason.strip():
            raise GovernanceError("a status change carries who made it and why")
        row = self.session.scalar(select(ForecastMethodModel).where(
            ForecastMethodModel.method_key == method_key))
        if row is None:
            raise GovernanceError(f"no method registered under {method_key!r}")
        if status == ForecastMethodStatus.PRODUCTION_ELIGIBLE:
            if row.is_flat_control:
                raise GovernanceError(f"{method_key} is a flat control and cannot be Production Eligible")
            if row.status != ForecastMethodStatus.PRODUCTION_ELIGIBLE.value:
                raise GovernanceError(
                    f"{method_key} becomes Production Eligible only through an administrator's "
                    "promotion (promote), which is recorded")
        return self._change(row, status, actor=actor, reason=reason)

    def promote(self, method_key: str, *, actor: str, reason: str) -> ChallengerApproval:
        """An administrator makes a Challenger Production Eligible. Explicit, recorded, never automatic.

        Promotion does not select the method anywhere; it only lets it compete.
        """
        approval = ChallengerApproval(method_id="", actor=actor, reason=reason, approved_at=_now())
        row = self.session.scalar(select(ForecastMethodModel).where(
            ForecastMethodModel.method_key == method_key))
        if row is None:
            raise GovernanceError(f"no method registered under {method_key!r}")
        if row.is_flat_control:
            raise GovernanceError(f"{method_key} is a flat control and cannot be Production Eligible")
        if row.status != ForecastMethodStatus.CHALLENGER.value:
            raise GovernanceError(f"{method_key} is {row.status}, not a Challenger")
        approval = ChallengerApproval(method_id=row.id, actor=actor, reason=reason,
                                      approved_at=approval.approved_at, id=approval.id)
        self.session.add(ChallengerApprovalModel(
            id=approval.id, method_id=row.id, actor=actor, reason=reason,
            approved_at=approval.approved_at))
        self._change(row, ForecastMethodStatus.PRODUCTION_ELIGIBLE, actor=actor, reason=reason)
        return approval

    def approvals(self, method_key: str) -> list[ChallengerApproval]:
        row = self.session.scalar(select(ForecastMethodModel).where(
            ForecastMethodModel.method_key == method_key))
        if row is None:
            return []
        rows = self.session.scalars(select(ChallengerApprovalModel).where(
            ChallengerApprovalModel.method_id == row.id).order_by(ChallengerApprovalModel.approved_at))
        return [ChallengerApproval(method_id=r.method_id, actor=r.actor, reason=r.reason,
                                   approved_at=r.approved_at, id=r.id) for r in rows]

    def history(self, method_key: str) -> list[dict]:
        row = self.session.scalar(select(ForecastMethodModel).where(
            ForecastMethodModel.method_key == method_key))
        return list(row.status_history or []) if row else []

    def _change(self, row: ForecastMethodModel, status: ForecastMethodStatus, *, actor: str,
                reason: str) -> ForecastMethod:
        now = _now()
        history = list(row.status_history or [])
        history.append({"at": now.isoformat(), "from": row.status, "to": status.value,
                        "actor": actor, "reason": reason})
        row.status = status.value
        row.status_history = history
        row.updated_at = now
        self.session.flush()
        return _to_domain(row)


# ----------------------------------------------------------------------------- selections

def _selection(row: ForecastMethodSelectionModel) -> ForecastMethodSelection:
    return ForecastMethodSelection(
        identity_id=row.identity_id, horizon_weeks=row.horizon_weeks, method_id=row.method_id,
        method_version=row.method_version, source=SelectionSource(row.source),
        selected_at=row.selected_at, review_id=row.review_id, actor=row.actor,
        reason=row.reason or "", active=bool(row.active), id=row.id)


class ForecastSelectionRepository:
    """The method in force per series and horizon. One active row per pair; the rest are history."""

    def __init__(self, session) -> None:
        self.session = session

    def get_active(self, identity_id: str, horizon_weeks: int) -> ForecastMethodSelection | None:
        row = self.session.scalar(select(ForecastMethodSelectionModel).where(
            ForecastMethodSelectionModel.identity_id == identity_id,
            ForecastMethodSelectionModel.horizon_weeks == horizon_weeks,
            ForecastMethodSelectionModel.active.is_(True)))
        return _selection(row) if row else None

    def list_active(self) -> list[ForecastMethodSelection]:
        rows = self.session.scalars(select(ForecastMethodSelectionModel).where(
            ForecastMethodSelectionModel.active.is_(True)).order_by(
            ForecastMethodSelectionModel.identity_id, ForecastMethodSelectionModel.horizon_weeks))
        return [_selection(row) for row in rows]

    def history(self, identity_id: str, horizon_weeks: int) -> list[ForecastMethodSelection]:
        rows = self.session.scalars(select(ForecastMethodSelectionModel).where(
            ForecastMethodSelectionModel.identity_id == identity_id,
            ForecastMethodSelectionModel.horizon_weeks == horizon_weeks).order_by(
            ForecastMethodSelectionModel.selected_at))
        return [_selection(row) for row in rows]

    def set_active(self, selection: ForecastMethodSelection) -> ForecastMethodSelection:
        """Make this the selection for its series and horizon; the previous one becomes history."""
        now = _now()
        for row in self.session.scalars(select(ForecastMethodSelectionModel).where(
                ForecastMethodSelectionModel.identity_id == selection.identity_id,
                ForecastMethodSelectionModel.horizon_weeks == selection.horizon_weeks,
                ForecastMethodSelectionModel.active.is_(True))):
            row.active = False
            row.superseded_at = now
        # The old row stops being active BEFORE the new one exists: the store holds at most one active
        # selection per pair (unique partial index, v0.5.65), so the order is written, not left to the
        # unit of work.
        self.session.flush()
        self.session.add(ForecastMethodSelectionModel(
            id=selection.id, identity_id=selection.identity_id, horizon_weeks=selection.horizon_weeks,
            method_id=selection.method_id, method_version=selection.method_version,
            source=selection.source.value, review_id=selection.review_id, active=True,
            selected_at=selection.selected_at, actor=selection.actor, reason=selection.reason))
        self.session.flush()
        return selection


# ----------------------------------------------------------------------------- overrides

def _override(row: ForecastMethodOverrideModel) -> ForecastMethodOverride:
    return ForecastMethodOverride(
        identity_id=row.identity_id, horizon_weeks=row.horizon_weeks, method_id=row.method_id,
        actor=row.actor, reason=row.reason, created_at=row.created_at, removed_at=row.removed_at,
        removed_by=row.removed_by or "", removal_reason=row.removal_reason or "", id=row.id)


class ForecastOverrideRepository:
    """An administrator's choice per series and horizon. Never replaced automatically."""

    def __init__(self, session) -> None:
        self.session = session

    def get_active(self, identity_id: str, horizon_weeks: int) -> ForecastMethodOverride | None:
        row = self.session.scalar(select(ForecastMethodOverrideModel).where(
            ForecastMethodOverrideModel.identity_id == identity_id,
            ForecastMethodOverrideModel.horizon_weeks == horizon_weeks,
            ForecastMethodOverrideModel.removed_at.is_(None)))
        return _override(row) if row else None

    def list_active(self) -> list[ForecastMethodOverride]:
        rows = self.session.scalars(select(ForecastMethodOverrideModel).where(
            ForecastMethodOverrideModel.removed_at.is_(None)))
        return [_override(row) for row in rows]

    def set(self, override: ForecastMethodOverride) -> ForecastMethodOverride:
        """Put an override in force for one series and horizon, closing any earlier one there."""
        for row in self.session.scalars(select(ForecastMethodOverrideModel).where(
                ForecastMethodOverrideModel.identity_id == override.identity_id,
                ForecastMethodOverrideModel.horizon_weeks == override.horizon_weeks,
                ForecastMethodOverrideModel.removed_at.is_(None))):
            row.removed_at = override.created_at
            row.removed_by = override.actor
            row.removal_reason = f"replaced: {override.reason}"
        self.session.add(ForecastMethodOverrideModel(
            id=override.id, identity_id=override.identity_id, horizon_weeks=override.horizon_weeks,
            method_id=override.method_id, actor=override.actor, reason=override.reason,
            created_at=override.created_at))
        self.session.flush()
        return override

    def remove(self, identity_id: str, horizon_weeks: int, *, actor: str, reason: str) -> bool:
        if not actor.strip() or not reason.strip():
            raise GovernanceError("removing an override carries who did it and why")
        row = self.session.scalar(select(ForecastMethodOverrideModel).where(
            ForecastMethodOverrideModel.identity_id == identity_id,
            ForecastMethodOverrideModel.horizon_weeks == horizon_weeks,
            ForecastMethodOverrideModel.removed_at.is_(None)))
        if row is None:
            return False
        row.removed_at = _now()
        row.removed_by = actor
        row.removal_reason = reason
        self.session.flush()
        return True


# ----------------------------------------------------------------------------- reviews

class ModelFitReviewRepository:
    """Reviews and their permanent results."""

    def __init__(self, session) -> None:
        self.session = session

    def open(self, review: ModelFitReview) -> ModelFitReview:
        self.session.add(ModelFitReviewModel(
            id=review.id, cutoff=review.cutoff, started_at=review.started_at,
            finished_at=review.finished_at, status=review.status.value, actor=review.actor,
            notes=review.notes))
        self.session.flush()
        return review

    def close(self, review_id: str, status: ReviewStatus, *, notes: str = "") -> None:
        row = self.session.get(ModelFitReviewModel, review_id)
        if row is None:
            raise GovernanceError(f"no review {review_id}")
        if row.status != ReviewStatus.RUNNING.value:
            raise GovernanceError(f"review {review_id} is already {row.status}; a review closes once")
        row.status = status.value
        row.finished_at = _now()
        if notes:
            row.notes = notes
        self.session.flush()

    def get(self, review_id: str) -> ModelFitReview | None:
        row = self.session.get(ModelFitReviewModel, review_id)
        if row is None:
            return None
        return ModelFitReview(cutoff=row.cutoff, started_at=row.started_at,
                              status=ReviewStatus(row.status), actor=row.actor,
                              finished_at=row.finished_at, notes=row.notes or "", id=row.id)

    def latest_successful(self) -> ModelFitReview | None:
        row = self.session.scalar(select(ModelFitReviewModel).where(
            ModelFitReviewModel.status == ReviewStatus.SUCCEEDED.value).order_by(
            ModelFitReviewModel.finished_at.desc()))
        return self.get(row.id) if row else None

    def add_result(self, result: ModelFitResult) -> ModelFitResult:
        self.session.add(ModelFitResultModel(
            id=result.id, review_id=result.review_id, identity_id=result.identity_id,
            horizon_weeks=result.horizon_weeks, method_id=result.method_id,
            method_version=result.method_version, execution_status=result.execution_status,
            matched_mae=result.matched_mae, directional_accuracy=result.directional_accuracy,
            matched_dates=result.matched_dates, rank=result.rank, details=dict(result.details)))
        self.session.flush()
        return result

    def results(self, review_id: str) -> list[ModelFitResult]:
        rows = self.session.scalars(select(ModelFitResultModel).where(
            ModelFitResultModel.review_id == review_id).order_by(
            ModelFitResultModel.identity_id, ModelFitResultModel.horizon_weeks, ModelFitResultModel.rank))
        return [ModelFitResult(review_id=r.review_id, identity_id=r.identity_id,
                               horizon_weeks=r.horizon_weeks, method_id=r.method_id,
                               method_version=r.method_version, execution_status=r.execution_status,
                               matched_mae=r.matched_mae, directional_accuracy=r.directional_accuracy,
                               matched_dates=r.matched_dates, rank=r.rank, details=dict(r.details or {}),
                               id=r.id) for r in rows]


# ----------------------------------------------------------------------------- the seed

@dataclass(frozen=True)
class SeedEntry:
    method_key: str
    name: str
    kind: str
    status: ForecastMethodStatus
    config: dict
    notes: str = ""
    commercial_use: str = "UNKNOWN"      # APPROVED | RESEARCH_ONLY | UNKNOWN - the licence, apart from status


# The research configurations of the CR (implementation plan, Tasks 3 and 5; Charlie's decisions
# of 19 October 2026, section 4). All enter as Challengers: scored in every review, promoted only
# by an administrator after the first application-native validation. Assets are the research's.
_CHRONOS2 = "amazon/chronos-2"
_BOLT = "amazon/chronos-bolt-tiny"
_TIMESFM = "google/timesfm-2.5-200m-pytorch"

RESEARCH_CONFIGURATIONS: tuple[SeedEntry, ...] = (
    # Universal (the two arithmetic configurations are the engine's own methods, seeded from its registry below)
    SeedEntry("chronos2_univariate", "Chronos-2, univariate", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"model": _CHRONOS2, "input": "target history", "package": "chronos-forecasting==2.3.2"}),
    SeedEntry("chronos2_recent_36m", "Chronos-2, recent 36 months", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"model": _CHRONOS2, "input": "target history", "context_months": 36}),
    SeedEntry("chronos2_log_price", "Chronos-2, log price", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"model": _CHRONOS2, "input": "log(target history)"}),
    SeedEntry("chronos_bolt_tiny", "Chronos-Bolt tiny", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"model": _BOLT, "input": "target history"}),
    SeedEntry("timesfm_2_5", "TimesFM 2.5 (200M)", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"model": _TIMESFM, "input": "target history",
               "research_commit": "e51928e27119cb17bebc005be2696b75e0a9e688",
               "note": "TimesFM 2.5 200M checkpoint; never TimesFM 3.0 weights under this CR"}),
    SeedEntry("foundation_blend_equal", "Foundation blend, equal weights", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"members": ["chronos2_univariate", "chronos_bolt_tiny", "timesfm_2_5"], "weights": "equal"}),
    SeedEntry("foundation_blend_adaptive", "Foundation blend, adaptive weights", UNIVERSAL, ForecastMethodStatus.CHALLENGER,
              {"members": ["chronos2_univariate", "chronos_bolt_tiny", "timesfm_2_5"],
               "weights": "from outcomes matured by the forecast cutoff"}),
    # Conditional: INELIGIBLE rather than FAILED when the supporting data are absent
    SeedEntry("lightgbm_related", "LightGBM on target and related series", CONDITIONAL, ForecastMethodStatus.CHALLENGER,
              {"needs": "related series visible at the cutoff"}),
    SeedEntry("sarimax_related", "SARIMAX with related-series regressors", CONDITIONAL, ForecastMethodStatus.CHALLENGER,
              {"needs": "related series visible at the cutoff"}),
    SeedEntry("chronos2_related_market", "Chronos-2 with related market as covariate", CONDITIONAL, ForecastMethodStatus.CHALLENGER,
              {"model": _CHRONOS2, "needs": "one related series visible at the cutoff"}),
    SeedEntry("chronos2_joint_network", "Chronos-2, joint chemical network", CONDITIONAL, ForecastMethodStatus.CHALLENGER,
              {"model": _CHRONOS2, "needs": "the chemical network's series visible at the cutoff"}),
    SeedEntry("intelligence_enhanced", "Intelligence-enhanced (features and events)", CONDITIONAL, ForecastMethodStatus.CHALLENGER,
              {"needs": "feature_observation and event_record inputs for the series"}),
    # Retired, kept visible (Charlie, WP1 gate decision 2, 19 October 2026): it lost to naive on
    # 102 origins and is not in the engine's default registry. INACTIVE: in the history, out of
    # selection and out of challenger evaluation, unless deliberately reactivated.
    SeedEntry("feedstock_regression", "Feedstock regression", UNIVERSAL, ForecastMethodStatus.INACTIVE,
              {"engine_model": "feedstock_regression"},
              "retired: lost to naive_last on 102 origins; reactivate only by decision"),
)


def _engine_entries(model_registry) -> list[SeedEntry]:
    """The methods the engine runs today, from its own registry: eligible as they stand, controls as controls."""
    out = []
    for spec in model_registry.specs():
        licence = "APPROVED" if getattr(spec, "commercial", False) else "RESEARCH_ONLY"
        if spec.key in FLAT_CONTROL_KEYS:
            out.append(SeedEntry(spec.key, getattr(spec, "display_name", None) or spec.key, CONTROL,
                                 ForecastMethodStatus.INACTIVE, {"engine_model": spec.key},
                                 "flat control: scored as a reference, never selectable", licence))
        elif spec.is_control:
            # A benchmark in the engine's sense (ETS) that is not a flat control. The CR bars only
            # unchanged-price controls from production; whether this one earns a place is an
            # administrator's call after the first review, so it enters as a Challenger.
            out.append(SeedEntry(spec.key, getattr(spec, "display_name", None) or spec.key, UNIVERSAL,
                                 ForecastMethodStatus.CHALLENGER, {"engine_model": spec.key, "version": spec.version},
                                 "the engine's benchmark; not flat, so it may be promoted", licence))
        else:
            out.append(SeedEntry(spec.key, getattr(spec, "display_name", None) or spec.key, UNIVERSAL,
                                 ForecastMethodStatus.PRODUCTION_ELIGIBLE,
                                 {"engine_model": spec.key, "version": spec.version},
                                 "runs in the engine's tournament today",
                                 "APPROVED" if getattr(spec, "commercial", False) else "RESEARCH_ONLY"))
    return out


def seed_methods(session, model_registry=None, *, entries: Iterable[SeedEntry] | None = None) -> dict:
    """Register every method once. Rerunning changes nothing an administrator has decided.

    Returns {"added": [...], "kept": [...]}.
    """
    registry = ForecastMethodRegistry(session)
    if entries is None:
        entries = list(_engine_entries(model_registry) if model_registry is not None else []) + list(RESEARCH_CONFIGURATIONS)
    from sqlalchemy.exc import IntegrityError

    added, kept = [], []
    for entry in entries:
        if registry.get(entry.method_key) is not None:
            kept.append(entry.method_key)
            continue
        method = ForecastMethod(
            method_key=entry.method_key, name=entry.name, kind=entry.kind, status=entry.status,
            config=dict(entry.config), notes=entry.notes,
            commercial_use=entry.commercial_use)
        # Two engines seeding at once (a factory beside a module-level app, or two processes) must
        # not fail: the unique key on method_key decides, and the loser keeps what the winner wrote.
        try:
            with session.begin_nested():
                registry.add(method, actor="SYSTEM", reason="seeded from the approved method list")
        except IntegrityError:
            kept.append(entry.method_key)
            continue
        added.append(entry.method_key)
    session.flush()
    return {"added": added, "kept": kept}
