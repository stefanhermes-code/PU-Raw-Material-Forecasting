from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from .domain import MarketIdentity
from .repositories import MarketRepository

@dataclass(frozen=True)
class PriceHistory:
    identity: MarketIdentity
    as_of: datetime
    observed: tuple[dict, ...]
    real_observation_count: int
    synthetic_observation_count: int = 0

def build_price_history(identity: MarketIdentity, as_of: datetime, repo: MarketRepository) -> PriceHistory:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    def aware(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    rows = [r for r in repo.list_price_observations(identity.key) if aware(r["publication_at"]) <= aware(as_of)]
    return PriceHistory(identity, as_of, tuple(rows), len(rows), 0)
