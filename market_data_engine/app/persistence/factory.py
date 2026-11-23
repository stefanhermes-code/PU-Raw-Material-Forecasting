from __future__ import annotations

from app.config import Settings
from app.ingestion.raw_store import create_raw_store
from app.persistence.cloud_service import CloudMarketDataService
from app.persistence.supabase_store import SupabaseMarketStore
from app.sources.registry import SourceRegistry


def create_collection_service(settings: Settings | None = None, *, market_store=None, raw_store=None):
    settings = settings or Settings()
    if settings.raw_store_backend.lower() == "supabase":
        settings.validate_cloud_storage()
        market_store = market_store or SupabaseMarketStore(settings.supabase_url, settings.supabase_secret_key)
        raw_store = raw_store or create_raw_store(settings)
        return CloudMarketDataService(
            market_store=market_store,
            raw_store=raw_store,
            registry=SourceRegistry.expanded(),
        )

    # Preserve the existing local application/service path for Test Environment use.
    from app.main import app

    return app.state.market_data_service
