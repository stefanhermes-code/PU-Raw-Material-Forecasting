from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class MarketIdentity:
    product_family: str
    grade: str
    origin: str
    destination: str
    price_basis: str
    packaging: str
    shipment_size: str
    currency: str

    @property
    def key(self) -> str:
        return " | ".join(
            [
                self.product_family,
                self.grade,
                self.origin,
                self.destination,
                self.price_basis,
                self.packaging,
                self.shipment_size,
                self.currency,
            ]
        )


@dataclass(frozen=True)
class SourceRecord:
    source_key: str
    provider: str
    source_type: str
    rights_class: str
    reliability_class: str


@dataclass(frozen=True)
class PriceObservation:
    identity: MarketIdentity
    observation_date: date
    publication_at: datetime
    ingested_at: datetime
    value: float
    unit: str
    observation_class: str
    source_key: str
    identity_complete: bool
    publication_date_verified: bool = False


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    product_family: str
    geography: str
    event_type: str
    publication_at: datetime
    ingested_at: datetime
    start_at: Optional[datetime]
    expected_end_at: Optional[datetime]
    direction: str
    severity: str
    confidence: float
    source_key: str
    activation_state: str


@dataclass(frozen=True)
class ForecastVintage:
    vintage_id: str
    identity_key: str
    as_of: datetime
    horizon_weeks: int
    p10: Optional[float]
    p25: Optional[float]
    p50: float
    p75: Optional[float]
    p90: Optional[float]
    release_state: str
    model_key: str
    model_version: str
    evidence_snapshot_json: str
    target_date: Optional[date] = None


@dataclass(frozen=True)
class DiscoveryCandidate:
    candidate_id: str
    title: str
    google_news_url: str
    publisher: str
    publisher_url: str
    published_at: Optional[datetime]
    discovered_at: datetime
    query_key: str
    raw_summary: str
    publisher_home_url: str = "UNKNOWN"


@dataclass(frozen=True)
class SourceDecision:
    source_tier: str
    rights_class: str


@dataclass(frozen=True)
class CandidateStateRecord:
    candidate_id: str
    state: str
    changed_at: datetime
    changed_by: str
    reason: str


@dataclass(frozen=True)
class ForecastOutcome:
    outcome_id: str
    vintage_id: str
    identity_key: str
    horizon_weeks: int
    target_date: date
    actual_observation_date: date
    actual_value: float
    forecast_p50: float
    absolute_error: float
    signed_error: float
    percentage_error: Optional[float]
    direction_correct: Optional[bool]
    interval_covered: Optional[bool]
    scored_at: datetime
