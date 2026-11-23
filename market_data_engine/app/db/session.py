"""How the engine opens the market store.

SQLite's default journal mode lets one writer lock out every reader for as long as its write
transaction is open. The collector writes for minutes at a time, and the Forecast page reads while
it does - so a page opened during a collection asked for the forecast, waited behind the collector's
lock until pysqlite's five-second default gave up, and fell back to "the forecast is being prepared"
on a market whose numbers were sitting in the file the whole time.

WAL changes that: readers and one writer work at the same time, each reader seeing the last
committed state rather than waiting for the writer to finish. The busy timeout covers the one case
WAL does not - two writers meeting at a checkpoint - by waiting instead of raising.

Both are set per connection, because SQLite forgets neither: journal_mode is a property of the file
and survives, and busy_timeout is per connection and does not.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.config import Settings

# Long enough to outlast a checkpoint, short enough that a genuinely stuck write still surfaces as
# an error rather than a page that hangs.
BUSY_TIMEOUT_MS = 10_000


# The engine's share of the pooler's client sessions, stated here rather than imported from the
# application: this package is a service of its own and does not reach into the one in front of it.
# The two shares are sized together - see raw_material_forecasting.platform_store, which holds the
# measurement and the rest of the reasoning.
#
# Measured on the live store, 23 November 2026, while the deployment was refusing to connect:
#   FATAL: (EMAXCONNSESSION) max clients reached in session mode
#          - max clients are limited to pool_size: 15
# Fifteen for everything that connects at once. SQLAlchemy's default of five plus ten overflow
# would take the lot on its own.
POOL_SIZE = 3
MAX_OVERFLOW = 2
# Supabase's transaction-mode pooler cannot promise the same server connection twice, so psycopg's
# prepared statements have to be off there. Moving the URL to :6543 is then the whole of it.
TRANSACTION_POOLER_PORT = ":6543"


def _postgres_pool(url: str = "") -> dict:
    settings: dict = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_timeout": 10,
    }
    if TRANSACTION_POOLER_PORT in url:
        settings["connect_args"] = {"prepare_threshold": None}
    return settings


def create_db_engine(database_url: str | None = None):
    url = database_url or Settings().database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, pool_pre_ping=True)
    else:
        engine = create_engine(url, **_postgres_pool(url))
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(connection, _record):  # pragma: no cover - exercised through the engine
        cursor = connection.cursor()
        try:
            # An in-memory database has no file to keep a WAL beside, and asking for one there
            # fails; everything else is a real file and takes it.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            # WAL makes each commit a write to the log rather than to the database. NORMAL stops
            # fsyncing on every one of them, which is what makes a collection run of thousands of
            # small commits bearable; the log still survives a crash of the application.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def create_session_factory(database_url: str | None = None):
    return sessionmaker(bind=create_db_engine(database_url), autoflush=False, expire_on_commit=False)
