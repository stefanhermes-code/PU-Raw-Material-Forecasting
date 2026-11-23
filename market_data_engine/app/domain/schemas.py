from __future__ import annotations
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class MarketIdentity(BaseModel):
    product_family: str
    product_grade_or_chemistry: str | None = None
    origin: str | None = None
    destination: str | None = None
    market_geography: str | None = None
    incoterm_or_commercial_basis: str | None = None
    packaging: str | None = None
    currency: str
    unit: str
    observation_type: str
    comparability_group: str

    @property
    def identity_key(self) -> str:
        from app.domain.series_key import series_key   # local: series_key imports this module
        return series_key(self)


class SourceItem(BaseModel):
    source_url: str

class ExtractedRecord(BaseModel):
    kind: str
    data: dict
    source_url: str
    publication_date: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PriceObservation(BaseModel):
    observation_id: str
    source_id: str
    evidence_id: str
    identity: MarketIdentity
    price: float
    price_low: float | None = None
    price_high: float | None = None
    observation_date: datetime | None = None
    publication_date: datetime | None = None
    available_from: datetime
    weight: float = 1.0

class MarketReferencePoint(BaseModel):
    identity_key: str
    reference_value: float
    observation_ids: list[str]
    version: int = 1
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
