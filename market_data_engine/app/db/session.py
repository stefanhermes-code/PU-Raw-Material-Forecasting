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


def create_db_engine(database_url: str | None = None):
    url = database_url or Settings().database_url
    engine = create_engine(url, pool_pre_ping=True)
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
