from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, MetaData, String, Table, Text, UniqueConstraint, Column, select, insert, update
from sqlalchemy.engine import Engine

from .domain import (
    CandidateStateRecord,
    DiscoveryCandidate,
    ForecastOutcome,
    ForecastVintage,
    MarketEvent,
    MarketIdentity,
    PriceObservation,
    SourceRecord,
)


class ImmutableRecordError(RuntimeError):
    pass


metadata = MetaData()
source_registry = Table(
    "source_registry", metadata,
    Column("source_key", String, primary_key=True),
    Column("provider", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("rights_class", String, nullable=False),
    Column("reliability_class", String, nullable=False),
)
market_identity = Table(
    "market_identity", metadata,
    Column("identity_key", String, primary_key=True),
    Column("product_family", String, nullable=False),
    Column("grade", String, nullable=False),
    Column("origin", String, nullable=False),
    Column("destination", String, nullable=False),
    Column("price_basis", String, nullable=False),
    Column("packaging", String, nullable=False),
    Column("shipment_size", String, nullable=False),
    Column("currency", String, nullable=False),
)
price_observation = Table(
    "price_observation", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("identity_key", String, nullable=False),
    Column("observation_date", Date, nullable=False),
    Column("publication_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String, nullable=False),
    Column("observation_class", String, nullable=False),
    Column("source_key", String, nullable=False),
    Column("identity_complete", Boolean, nullable=False),
    Column("publication_date_verified", Boolean, nullable=False, default=False),
    UniqueConstraint("identity_key", "observation_date", "source_key", "observation_class", "value", name="uq_price_fingerprint"),
)
market_event = Table(
    "market_event", metadata,
    Column("event_id", String, primary_key=True),
    Column("product_family", String, nullable=False),
    Column("geography", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("publication_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("start_at", DateTime(timezone=True)),
    Column("expected_end_at", DateTime(timezone=True)),
    Column("direction", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("source_key", String, nullable=False),
    Column("activation_state", String, nullable=False),
)
discovery_candidate = Table(
    "discovery_candidate", metadata,
    Column("candidate_id", String, primary_key=True),
    Column("title", Text, nullable=False),
    Column("google_news_url", Text, nullable=False),
    Column("publisher", String, nullable=False),
    Column("publisher_url", Text, nullable=False),
    Column("publisher_home_url", Text, nullable=False, default="UNKNOWN"),
    Column("published_at", DateTime(timezone=True)),
    Column("discovered_at", DateTime(timezone=True), nullable=False),
    Column("query_key", Text, nullable=False),
    Column("raw_summary", Text, nullable=False),
)
candidate_state_history = Table(
    "candidate_state_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("candidate_id", String, nullable=False),
    Column("state", String, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
    Column("changed_by", String, nullable=False),
    Column("reason", Text, nullable=False),
)
forecast_vintage = Table(
    "forecast_vintage", metadata,
    Column("vintage_id", String, primary_key=True),
    Column("identity_key", String, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("target_date", Date),
    Column("horizon_weeks", Integer, nullable=False),
    Column("p10", Float), Column("p25", Float), Column("p50", Float, nullable=False), Column("p75", Float), Column("p90", Float),
    Column("release_state", String, nullable=False),
    Column("model_key", String, nullable=False), Column("model_version", String, nullable=False),
    Column("evidence_snapshot_json", Text, nullable=False),
)
forecast_outcome = Table(
    "forecast_outcome", metadata,
    Column("outcome_id", String, primary_key=True),
    Column("vintage_id", String, nullable=False),
    Column("identity_key", String, nullable=False),
    Column("horizon_weeks", Integer, nullable=False),
    Column("target_date", Date, nullable=False),
    Column("actual_observation_date", Date, nullable=False),
    Column("actual_value", Float, nullable=False),
    Column("forecast_p50", Float, nullable=False),
    Column("absolute_error", Float, nullable=False),
    Column("signed_error", Float, nullable=False),
    Column("percentage_error", Float),
    Column("direction_correct", Boolean),
    Column("interval_covered", Boolean),
    Column("scored_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("vintage_id", name="uq_outcome_vintage"),
)


def _dt(v):
    return v


class MarketRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        metadata.create_all(engine)

    def upsert_source(self, src: SourceRecord) -> None:
        with self.engine.begin() as c:
            row = c.execute(select(source_registry).where(source_registry.c.source_key == src.source_key)).mappings().first()
            values = asdict(src)
            if row:
                c.execute(update(source_registry).where(source_registry.c.source_key == src.source_key).values(**values))
            else:
                c.execute(insert(source_registry).values(**values))

    def get_source(self, source_key: str) -> SourceRecord | None:
        with self.engine.connect() as c:
            r = c.execute(select(source_registry).where(source_registry.c.source_key == source_key)).mappings().first()
        return SourceRecord(**r) if r else None

    def _ensure_identity(self, identity: MarketIdentity) -> None:
        values = {"identity_key": identity.key, **asdict(identity)}
        with self.engine.begin() as c:
            exists = c.execute(select(market_identity.c.identity_key).where(market_identity.c.identity_key == identity.key)).first()
            if not exists:
                c.execute(insert(market_identity).values(**values))

    def price_observation_exists(self, obs: PriceObservation) -> bool:
        q = select(price_observation.c.id).where(
            price_observation.c.identity_key == obs.identity.key,
            price_observation.c.observation_date == obs.observation_date,
            price_observation.c.source_key == obs.source_key,
            price_observation.c.observation_class == obs.observation_class,
            price_observation.c.value == obs.value,
        )
        with self.engine.connect() as c:
            return c.execute(q).first() is not None

    def insert_price_observation(self, obs: PriceObservation) -> bool:
        self._ensure_identity(obs.identity)
        if self.price_observation_exists(obs):
            return False
        with self.engine.begin() as c:
            c.execute(insert(price_observation).values(
                identity_key=obs.identity.key,
                observation_date=obs.observation_date,
                publication_at=obs.publication_at,
                ingested_at=obs.ingested_at,
                value=obs.value,
                unit=obs.unit,
                observation_class=obs.observation_class,
                source_key=obs.source_key,
                identity_complete=obs.identity_complete,
                publication_date_verified=obs.publication_date_verified,
            ))
        return True

    def list_price_observations(self, identity_key: str | None = None) -> list[dict]:
        q = select(price_observation)
        if identity_key:
            q = q.where(price_observation.c.identity_key == identity_key)
        q = q.order_by(price_observation.c.observation_date.asc(), price_observation.c.publication_at.asc())
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def insert_discovery_candidate(self, candidate: DiscoveryCandidate) -> bool:
        with self.engine.begin() as c:
            if c.execute(select(discovery_candidate.c.candidate_id).where(discovery_candidate.c.candidate_id == candidate.candidate_id)).first():
                return False
            c.execute(insert(discovery_candidate).values(**asdict(candidate)))
        return True

    def count_discovery_candidates(self) -> int:
        return len(self.list_discovery_candidates())

    def list_discovery_candidates(self) -> list[dict]:
        q = select(discovery_candidate).order_by(discovery_candidate.c.published_at.desc(), discovery_candidate.c.discovered_at.desc())
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def record_candidate_state(self, record: CandidateStateRecord) -> None:
        with self.engine.begin() as c:
            c.execute(insert(candidate_state_history).values(**asdict(record)))

    def get_candidate_state(self, candidate_id: str) -> str | None:
        q = select(candidate_state_history.c.state).where(candidate_state_history.c.candidate_id == candidate_id).order_by(candidate_state_history.c.changed_at.desc(), candidate_state_history.c.id.desc()).limit(1)
        with self.engine.connect() as c:
            r = c.execute(q).first()
        return r[0] if r else None

    def list_candidate_state_history(self, candidate_id: str) -> list[dict]:
        q = select(candidate_state_history).where(candidate_state_history.c.candidate_id == candidate_id).order_by(candidate_state_history.c.changed_at.asc(), candidate_state_history.c.id.asc())
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def insert_event(self, event: MarketEvent) -> bool:
        with self.engine.begin() as c:
            if c.execute(select(market_event.c.event_id).where(market_event.c.event_id == event.event_id)).first():
                return False
            c.execute(insert(market_event).values(**asdict(event)))
        return True

    def list_events(self, product_family: str | None = None) -> list[dict]:
        q = select(market_event)
        if product_family:
            q = q.where(market_event.c.product_family == product_family)
        q = q.order_by(market_event.c.publication_at.desc())
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def insert_forecast_vintage(self, v: ForecastVintage) -> bool:
        values = asdict(v)
        with self.engine.begin() as c:
            row = c.execute(select(forecast_vintage).where(forecast_vintage.c.vintage_id == v.vintage_id)).mappings().first()
            if row:
                existing = dict(row)
                def comparable(value):
                    if isinstance(value, datetime):
                        return value.replace(tzinfo=None)
                    return value
                if any(comparable(existing.get(k)) != comparable(val) for k, val in values.items()):
                    raise ImmutableRecordError(f"forecast vintage {v.vintage_id} is immutable")
                return False
            c.execute(insert(forecast_vintage).values(**values))
        return True

    def get_forecast_vintage(self, vintage_id: str) -> ForecastVintage | None:
        with self.engine.connect() as c:
            r = c.execute(select(forecast_vintage).where(forecast_vintage.c.vintage_id == vintage_id)).mappings().first()
        return ForecastVintage(**dict(r)) if r else None

    def list_forecast_vintages(self, identity_key: str | None = None) -> list[ForecastVintage]:
        q = select(forecast_vintage)
        if identity_key:
            q = q.where(forecast_vintage.c.identity_key == identity_key)
        q = q.order_by(forecast_vintage.c.as_of.desc(), forecast_vintage.c.horizon_weeks.asc())
        with self.engine.connect() as c:
            return [ForecastVintage(**dict(r)) for r in c.execute(q).mappings().all()]

    def forecast_outcome_exists(self, vintage_id: str) -> bool:
        with self.engine.connect() as c:
            return c.execute(select(forecast_outcome.c.outcome_id).where(forecast_outcome.c.vintage_id == vintage_id)).first() is not None

    def insert_forecast_outcome(self, outcome: ForecastOutcome) -> bool:
        if self.forecast_outcome_exists(outcome.vintage_id):
            return False
        with self.engine.begin() as c:
            c.execute(insert(forecast_outcome).values(**asdict(outcome)))
        return True

    def list_forecast_outcomes(self, identity_key: str | None = None) -> list[ForecastOutcome]:
        q = select(forecast_outcome)
        if identity_key:
            q = q.where(forecast_outcome.c.identity_key == identity_key)
        q = q.order_by(forecast_outcome.c.target_date.asc(), forecast_outcome.c.horizon_weeks.asc())
        with self.engine.connect() as c:
            return [ForecastOutcome(**dict(r)) for r in c.execute(q).mappings().all()]
