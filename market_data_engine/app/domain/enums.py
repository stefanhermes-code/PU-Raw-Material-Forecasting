from enum import Enum

class EvidenceClass(str, Enum):
    DIRECT_MARKET_PRICE = "A"
    HISTORICAL_LAGGING_MARKET_INDICATOR = "B"
    LEADING_EXPLANATORY_VARIABLE = "C"
    KNOWN_FUTURE_VARIABLE = "D"
    EVENT_REGIME_INFORMATION = "E"
    VALIDATION_ONLY = "F"
    REJECT_QUARANTINE = "G"

class RightsStatus(str, Enum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"

class RunState(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"

class EventState(str, Enum):
    ANNOUNCED = "ANNOUNCED"
    ACTIVE = "ACTIVE"
    EXTENDED = "EXTENDED"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"
    UNCONFIRMED = "UNCONFIRMED"


# ----------------------------------------------------------------------------------------------
# Forecast-method governance (Adaptive Forecast Method Selection CR, Workpackage 1, October 2026).

class ForecastMethodStatus(str, Enum):
    """What a forecasting method is allowed to do.

    PRODUCTION_ELIGIBLE can win a series/horizon and be selected by hand. CHALLENGER is scored in
    every Model Fit Review and can never be promoted automatically. INACTIVE is not run.
    """
    PRODUCTION_ELIGIBLE = "PRODUCTION_ELIGIBLE"
    CHALLENGER = "CHALLENGER"
    INACTIVE = "INACTIVE"


class SelectionSource(str, Enum):
    """Who chose the method a forecast was made with."""
    AUTOMATIC = "AUTOMATIC"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class ReviewStatus(str, Enum):
    """The state of one Model Fit Review. A FAILED review commits no selection change."""
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
