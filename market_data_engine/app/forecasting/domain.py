"""The governance of forecasting methods: what may be selected, by whom, on what evidence.

Adaptive Forecast Method Selection CR, Workpackage 1 (October 2026). These are the records the
store keeps about METHODS - as distinct from `forecast_vintage`, which keeps what a method said.
Nothing in this module changes a live forecast; it is the vocabulary the later workpackages act in.

Rules carried here rather than in a page or a service, so no caller can get round them:

- A flat control (no change, same as last year) can be run as a reference and never be Production
  Eligible, never be selected by hand, and never be recommended.
- A Model Fit Review has an identity that is fixed when it is created; results and selections
  point at it and it is never renumbered.
- A selection is one series, one horizon, one method, one source (automatic or manual override),
  and - when automatic - the successful review it rests on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.domain.enums import ForecastMethodStatus, ReviewStatus, SelectionSource

# The methods the engine runs today that are controls: they answer "what if the price simply does
# not move", and a method that cannot lose to them is not a method. Never selectable.
FLAT_CONTROL_KEYS: frozenset[str] = frozenset({"naive_last", "seasonal_naive"})
# The method a market is forecast with unless another has shown it is better there. Shown, not
# scored: `selection.propose_for` keeps it while the better score is inside the noise of the test.
STANDARD_METHOD_KEY = "ets"

UNIVERSAL = "UNIVERSAL"        # runs on the target series alone
CONDITIONAL = "CONDITIONAL"    # needs related series, features or events; INELIGIBLE without them
CONTROL = "CONTROL"            # a flat reference, scored and never selected


class GovernanceError(ValueError):
    """A rule of the method governance was about to be broken. Nothing was written."""


@dataclass(frozen=True)
class ForecastMethod:
    """One approved configuration of a forecasting method, as the registry knows it."""
    method_key: str
    name: str
    kind: str                                   # UNIVERSAL | CONDITIONAL | CONTROL
    status: ForecastMethodStatus
    version: str = "1"
    is_flat_control: bool = False
    # Whether the method's licence allows a forecast made with it to reach a customer. Held apart
    # from `status`: a method can be Production Eligible for the tournament and still be barred
    # from a customer screen by its licence (the engine's existing LicenceClass gate).
    commercial_use: str = "UNKNOWN"             # APPROVED | RESEARCH_ONLY | UNKNOWN
    config: dict = field(default_factory=dict)
    notes: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.method_key or not self.method_key.strip():
            raise GovernanceError("a method needs a key")
        if self.kind not in (UNIVERSAL, CONDITIONAL, CONTROL):
            raise GovernanceError(f"unknown method kind {self.kind!r}")
        flat = self.is_flat_control or self.kind == CONTROL or self.method_key in FLAT_CONTROL_KEYS
        if flat:
            object.__setattr__(self, "is_flat_control", True)
            object.__setattr__(self, "kind", CONTROL)
        if flat and self.status == ForecastMethodStatus.PRODUCTION_ELIGIBLE:
            raise GovernanceError(
                f"{self.method_key} is a flat control and cannot be Production Eligible")

    @property
    def selectable(self) -> bool:
        """Whether this method may win a series/horizon or be chosen by hand."""
        return self.status == ForecastMethodStatus.PRODUCTION_ELIGIBLE and not self.is_flat_control


@dataclass(frozen=True)
class ModelFitReview:
    """One run of the tournament over every eligible series and horizon, at one cutoff."""
    cutoff: datetime
    started_at: datetime
    status: ReviewStatus = ReviewStatus.RUNNING
    actor: str = "SYSTEM"
    finished_at: datetime | None = None
    notes: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ModelFitResult:
    """How one method did on one series and horizon inside one review."""
    review_id: str
    identity_id: str
    horizon_weeks: int
    method_id: str
    method_version: str
    execution_status: str                       # SUCCEEDED | FAILED | INELIGIBLE
    matched_mae: float | None = None
    directional_accuracy: float | None = None
    matched_dates: int = 0
    rank: int | None = None
    details: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ForecastMethodSelection:
    """The method in force for one series and one horizon."""
    identity_id: str
    horizon_weeks: int
    method_id: str
    method_version: str
    source: SelectionSource
    selected_at: datetime
    review_id: str | None = None                # the successful review an AUTOMATIC selection rests on
    actor: str = "SYSTEM"
    reason: str = ""
    active: bool = True
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.horizon_weeks <= 0:
            raise GovernanceError("a selection needs a horizon")
        if self.source == SelectionSource.AUTOMATIC and not self.review_id:
            raise GovernanceError("an automatic selection has to name the review it rests on")


@dataclass(frozen=True)
class ForecastMethodOverride:
    """An administrator's choice for one series and horizon. Never replaced automatically."""
    identity_id: str
    horizon_weeks: int
    method_id: str
    actor: str
    reason: str
    created_at: datetime
    removed_at: datetime | None = None
    removed_by: str = ""
    removal_reason: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.actor.strip() or not self.reason.strip():
            raise GovernanceError("an override carries who made it and why")

    @property
    def active(self) -> bool:
        return self.removed_at is None


@dataclass(frozen=True)
class ChallengerApproval:
    """An administrator promoting a Challenger to Production Eligible. Explicit, never automatic."""
    method_id: str
    actor: str
    reason: str
    approved_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.actor.strip() or not self.reason.strip():
            raise GovernanceError("a promotion carries who approved it and why")
