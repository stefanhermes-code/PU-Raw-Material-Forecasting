from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import create_database_engine
from .migrations import MigrationResult, migrate_china_pmdi_seed
from .repositories import MarketRepository


@dataclass(frozen=True)
class RuntimeRepository:
    repo: MarketRepository
    migration: MigrationResult
    database_path: Path


def open_runtime_repository(database_path: str | Path, seed_path: str | Path, now: datetime | None = None) -> RuntimeRepository:
    database_path = Path(database_path)
    engine = create_database_engine(database_path)
    repo = MarketRepository(engine)
    migration = migrate_china_pmdi_seed(seed_path, repo, ingested_at=now or datetime.now(timezone.utc))
    return RuntimeRepository(repo=repo, migration=migration, database_path=database_path)
