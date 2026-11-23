from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from .domain import MarketIdentity, PriceObservation, SourceRecord
from .repositories import MarketRepository
from . import units


@dataclass(frozen=True)
class MigrationResult:
    inserted: int
    skipped: int
    rejected: int


def _bool(v) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y"}


def migrate_china_pmdi_seed(csv_path: str | Path, repo: MarketRepository, ingested_at: datetime | None = None) -> MigrationResult:
    ingested_at = ingested_at or datetime.now(timezone.utc)
    df = pd.read_csv(csv_path)
    inserted = skipped = rejected = 0
    for _, row in df.iterrows():
        try:
            identity = MarketIdentity(
                product_family=str(row["product_family"]), grade=str(row["grade"]), origin=str(row["origin"]),
                destination=str(row["destination"]), price_basis=str(row["price_basis"]), packaging=str(row["packaging"]),
                shipment_size="UNKNOWN", currency=str(row["currency"]),
            )
            source_key = str(row["source_id"])
            repo.upsert_source(SourceRecord(
                source_key=source_key, provider=str(row.get("source_attribution", "UNKNOWN")),
                source_type="secondary_market_monitor", rights_class=str(row.get("rights_classification", "UNKNOWN")),
                reliability_class="TIER_B",
            ))
            obs = PriceObservation(
                identity=identity,
                observation_date=pd.Timestamp(row["observation_date"]).date(),
                publication_at=pd.Timestamp(row["publication_date"]).to_pydatetime().replace(tzinfo=timezone.utc),
                ingested_at=ingested_at,
                value=float(row["price_per_tonne"]), unit=f"USD/{units.MT}", observation_class="MARKET_TARGET",
                source_key=source_key,
                identity_complete=all(x != "UNKNOWN" for x in (identity.grade, identity.origin, identity.destination, identity.price_basis, identity.packaging, identity.shipment_size, identity.currency)),
                publication_date_verified=_bool(row.get("publication_date_verified", False)),
            )
            if repo.insert_price_observation(obs): inserted += 1
            else: skipped += 1
        except Exception:
            rejected += 1
    return MigrationResult(inserted, skipped, rejected)
