"""Where the platform-administration tables live.

v0.5.42: the same database as the market data engine - the Supabase project named by DATABASE_URL -
so there is one store, not a SQLite file beside it. The tables keep their pa_ prefix, which is what
separates them from the engine's tables in the one schema. Without DATABASE_URL (tests, a one-off
tool) the SQLite file is used as before, so nothing that builds its own engine changes.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

SQLITE_FALLBACK = Path("data/platform_admin.db")


def platform_store_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url and not url.startswith("sqlite"):
        return url
    SQLITE_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{SQLITE_FALLBACK}"


def platform_store_engine() -> Engine:
    url = platform_store_url()
    if url.startswith("sqlite"):
        return create_engine(url, future=True, connect_args={"check_same_thread": False})
    # pool_pre_ping: a pooled connection to Supabase can be closed from the other side between a
    # page's requests, and a stale one would surface as an error on the first click of the day.
    return create_engine(url, future=True, pool_pre_ping=True)
