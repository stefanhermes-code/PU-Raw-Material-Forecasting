"""Where the platform-administration tables live.

v0.5.42: the same database as the market data engine - the Supabase project named by DATABASE_URL -
so there is one store, not a SQLite file beside it. The tables keep their pa_ prefix, which is what
separates them from the engine's tables in the one schema. Without DATABASE_URL (tests, a one-off
tool) the SQLite file is used as before, so nothing that builds its own engine changes.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

SQLITE_FALLBACK = Path("data/platform_admin.db")


def platform_store_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url and not url.startswith("sqlite"):
        return url
    SQLITE_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{SQLITE_FALLBACK}"


# How many connections each part of the application may hold open at once.
#
# Measured on the live store, 23 November 2026, while the deployment was refusing to connect:
#
#   FATAL: (EMAXCONNSESSION) max clients reached in session mode
#          - max clients are limited to pool_size: 15
#
# Fifteen. Not per application - fifteen for everything that connects to this store at once: the
# deployment, this machine's own application, its collector, the delivery worker, any one-off
# script. SQLAlchemy's defaults are five plus ten overflow, so ONE unbounded engine can take the
# whole allowance, and a Streamlit process holds two of them - this one and the market-data
# engine's.
#
# So each is given a share it cannot exceed, and the shares add up to less than fifteen.
STORE_POOL = (2, 2)        # the platform store: small, and every page touches it
# The market data engine's share is (3, 2), stated in market_data_engine/app/db/session.py. That
# package is a service of its own and does not import from this one, so the two numbers are sized
# together here and written there. 2 + 2 + 3 + 2 = 9 of the fifteen, leaving room for this
# machine's own application and its collector.

# Supabase's pooler answers on two ports. 5432 is session mode, where a client holds a server
# connection for as long as it stays connected - fifteen of those and the sixteenth is refused.
# 6543 is transaction mode, where a connection is held for the length of a statement and hundreds
# of clients share the same fifteen. Transaction mode cannot promise the same server connection
# twice, so psycopg's prepared statements have to be off; this turns them off by itself, so moving
# the URL to 6543 is the whole of the change.
TRANSACTION_POOLER_PORT = ":6543"


def postgres_pool_settings(url: str, share: tuple[int, int]) -> dict:
    """What to hand `create_engine` for a Postgres store, given this part's share of the pooler."""
    size, overflow = share
    settings: dict = {
        # A pooled connection can be closed from the other side between a page's requests, and a
        # stale one would surface as an error on the first click of the day.
        "pool_pre_ping": True,
        # An engine now lives as long as the process, so a connection left open all night is
        # retired rather than offered to tomorrow's reader.
        "pool_recycle": 1800,
        "pool_size": size,
        "max_overflow": overflow,
        # Fail with a message rather than hang a page for thirty seconds waiting for a slot.
        "pool_timeout": 10,
    }
    if TRANSACTION_POOLER_PORT in url:
        settings["connect_args"] = {"prepare_threshold": None}
    return settings


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
            engine = create_engine(url, future=True, **postgres_pool_settings(url, STORE_POOL))
        _ENGINES[url] = engine
        return engine


# ----------------------------------------------------------------------------- who may read a table
#
# Supabase puts a web API in front of every table in the `public` schema. Whoever holds the
# project's anon key may read, change and delete a table through it - unless the table has row
# level security switched on, in which case, with no policy written, the API's roles see nothing.
#
# This application never uses that API. It connects to Postgres as the role that owns the tables,
# and row level security does not apply to an owner. So switching it on costs the application
# nothing and closes the API.
#
# But a table is created WITHOUT it. On 22 November 2026 Supabase's security advisor found 21
# tables served that way - the customers, the users and both tables of password digests among
# them - because they were tables this application had made for itself after somebody last
# protected the schema by hand. Nothing in the code did it (Stefan, 25 November 2026: "Do it now,
# dont wait"). The store has a trigger for it since that day; this is the same rule in the code,
# so that a store built from nothing is protected by the application that builds it.
APPLICATION_SCHEMAS = ("public", "forecasting")

# Tables of ours with row level security off. Only the ones this connection OWNS: it could not
# alter another role's table, and has no business deciding who reads one.
_UNPROTECTED = text("""
    select n.nspname, c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r', 'p')
      and n.nspname = any(:schemas)
      and not c.relrowsecurity
      and pg_get_userbyid(c.relowner) = current_user
    order by 1, 2
""")

_PROTECTED: set[tuple[Engine, str]] = set()
_PROTECTED_LOCK = threading.Lock()


def protect_tables(engine: Engine, made_by: str) -> list[str]:
    """Switch row level security on for every table of ours that lacks it. Returns their names.

    Called straight after each `create_all`, with a word for who made the tables. It runs once
    per engine for each of them - a question to the catalogue, and normally nothing to do - and
    it looks at every table in the application's schemas each time, not only the caller's, so the
    market-data engine's tables, which a migration creates, are covered by whichever part of the
    application starts first.

    It never stops a page. A table that cannot be altered - a lock held too long, a role that
    does not own it - is a line in the log, and the page goes on; the store's own trigger and
    the next start are still there. Only Postgres has the question at all: a SQLite file has no
    roles and nobody else to keep out.
    """
    if engine.dialect.name != "postgresql":
        return []
    with _PROTECTED_LOCK:
        if (engine, made_by) in _PROTECTED:
            return []
        _PROTECTED.add((engine, made_by))
    try:
        quote = engine.dialect.identifier_preparer.quote
        with engine.begin() as connection:
            # A table somebody is reading is not worth hanging a page for.
            connection.execute(text("set local lock_timeout = '3s'"))
            found = [(str(schema), str(table)) for schema, table in
                     connection.execute(_UNPROTECTED, {"schemas": list(APPLICATION_SCHEMAS)})]
            for schema, table in found:
                connection.execute(text(
                    f"alter table {quote(schema)}.{quote(table)} enable row level security"))
    except Exception:  # noqa: BLE001 - who may read a table must never decide whether a page opens
        log.exception("row level security could not be checked or switched on (tables made by %s)", made_by)
        return []
    names = [f"{schema}.{table}" for schema, table in found]
    if names:
        log.warning("row level security switched on for %s (found after %s made its tables)",
                    ", ".join(names), made_by)
    return names
