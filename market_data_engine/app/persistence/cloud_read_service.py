from __future__ import annotations


class CloudReadService:
    """Read-only cloud state consumed by the future Streamlit Cloud UI."""

    def __init__(self, store):
        self.store = store

    def snapshot(self) -> dict:
        return {
            "sources": self.store.list_source_state(),
            "observations": self.store.list_price_observations(),
            "identities": self.store.list_market_identities(),
            "references": self.store.list_market_references(),
        }
