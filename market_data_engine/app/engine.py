from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from app.classification.classifier import classify_record
from app.domain.enums import EvidenceClass
from app.domain.schemas import PriceObservation
from app.identity.resolver import resolve_identity
from app.ingestion.raw_store import RawStore
from app.quality.scoring import evidence_weight
from app.reference.calculator import ReferenceCalculator
from app.sources.registry import SourceRegistry, ensure_source_publishable

class MarketDataEngine:
    def __init__(self, raw_root, registry: SourceRegistry | None = None):
        self.registry = registry or SourceRegistry.default()
        self.raw = RawStore(raw_root)
        self.prices: list[PriceObservation] = []
        self.features: list[dict] = []
        self.events: list[dict] = []
        self.quarantine: list[dict] = []
        self.reference = ReferenceCalculator()

    def ingest_extracted(self, source_id: str, records):
        source = ensure_source_publishable(self.registry, source_id)
        for rec in records:
            raw_ref = self.raw.put(str(rec.data).encode("utf-8"), "application/x-python-dict")
            klass = classify_record(rec.data)
            if klass == EvidenceClass.DIRECT_MARKET_PRICE:
                identity = resolve_identity(rec.data)
                complete = all([identity.product_grade_or_chemistry, identity.incoterm_or_commercial_basis, identity.packaging])
                available = rec.publication_date or rec.collected_at or datetime.now(timezone.utc)
                self.prices.append(PriceObservation(
                    observation_id=str(uuid4()), source_id=source_id, evidence_id=raw_ref.content_hash,
                    identity=identity, price=float(rec.data["price"]),
                    observation_date=rec.data.get("observation_date"), publication_date=rec.publication_date,
                    available_from=available, weight=evidence_weight(source.reliability_level, complete)
                ))
            elif klass in {EvidenceClass.LEADING_EXPLANATORY_VARIABLE, EvidenceClass.HISTORICAL_LAGGING_MARKET_INDICATOR}:
                self.features.append({"source_id":source_id,"evidence_id":raw_ref.content_hash,"data":rec.data})
            elif klass in {EvidenceClass.KNOWN_FUTURE_VARIABLE, EvidenceClass.EVENT_REGIME_INFORMATION}:
                self.events.append({"source_id":source_id,"evidence_id":raw_ref.content_hash,"data":rec.data})
            else:
                self.quarantine.append({"source_id":source_id,"evidence_id":raw_ref.content_hash,"class":klass.value,"data":rec.data})

    def calculate_reference(self, identity_key: str):
        return self.reference.calculate(identity_key, self.prices)

    def prices_as_of(self, identity_key: str, cutoff: datetime):
        return [p for p in self.prices if p.identity.identity_key == identity_key and p.available_from <= cutoff]
