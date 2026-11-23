"""Where the platform-administration tables live.

v0.5.42: the same database as the market data engine - the Supabase project named by DATABASE_URL -
so there is one store, not a SQLite file beside it. The tables keep their pa_ prefix, which is what
separates them from the engine's tables in the one schema. Without DATABASE_URL (tests, a one-off
tool) the SQLite file is used as before, so nothing that builds its own engine changes.
"""
from __future__ import annotations

import os
import threading
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


# One engine per store, for the life of the process. See platform_store_engine.
_ENGINES: dict[str, Engine] = {}
_ENGINE_LOCK = threading.Lock()


def platform_store_engine() -> Engine:
    """The engine for this store, made once and shared.

    A SQLAlchemy Engine owns a connection pool. It is meant to be made once per process and handed
    round; making one per use makes a pool per use, and every pool opens its own connections.

    Streamlit runs the page top to bottom on every click, and `build_service` is called on every
    run - by Platform Admin, and by `resolve_runtime_access` for every customer. So until now each
    click built a new Engine, opened a new connection to Supabase and left the previous pool to the
    garbage collector, which releases connections whenever it gets round to it. On this machine,
    one reader at a time, that was invisible. On the deployment it is a connection per click per
    reader against a pooler with a limit, and the limit wins.

    Stefan, 23 November 2026, on the live deployment: an `OperationalError` the moment the company
    picker on Subscriptions was touched. Nothing was wrong with the store; the application had
    simply asked it for one connection too many.

    Keyed by URL, so a test that points DATABASE_URL somewhere else gets its own engine.
    """
    url = platform_store_url()
    with _ENGINE_LOCK:
        existing = _ENGINES.get(url)
        if existing is not None:
            return existing
        if url.startswith("sqlite"):
            engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
        else:
            # pool_pre_ping: a pooled connection to Supabase can be closed from the other side
            # between a page's requests, and a stale one would surface as an error on the first
            # click of the day. pool_recycle: now that an engine lives as long as the process, a
            # connection left open all night is retired rather than offered to tomorrow's reader.
            engine = create_engine(url, future=True, pool_pre_ping=True, pool_recycle=1800)
        _ENGINES[url] = engine
        return engine
