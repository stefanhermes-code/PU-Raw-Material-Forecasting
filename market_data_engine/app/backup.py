"""A daily copy of the market database, kept off the machine's own data folder.

Stefan, 13 October 2026: everything the application has collected lives in one SQLite file under
%LOCALAPPDATA%, outside OneDrive and backed up nowhere. "Yeah a backup is fine."

The copy is made with SQLite's own online backup, so it is consistent while the engine is running and
writing. One copy per calendar day; the newest `keep` are kept and older ones removed. The raw pages
(raw-market-data) are not copied: they are the evidence behind readings already in the database and
are large.
"""
from __future__ import annotations

import shutil
from contextlib import closing
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "market_data_engine_"


def database_path(database_url: str) -> Path:
    """The file behind a sqlite URL such as sqlite+pysqlite:///C:/Users/x/market_data_engine.db."""
    if not database_url.startswith("sqlite"):
        raise ValueError("only a SQLite database is copied this way")
    return Path(database_url.split(":///", 1)[1])


def backup_database(database_url: str, to: str | Path, *, keep: int = 7,
                    today: str | None = None) -> dict:
    # v0.5.39: the store is the Supabase project, which keeps its own backups; this file copy is
    # only for a SQLite store and says so instead of failing a collection run.
    if not database_url.startswith("sqlite"):
        return {"skipped": "store is not a SQLite file; Supabase keeps its own backups"}
    source = database_path(database_url)
    folder = Path(to)
    folder.mkdir(parents=True, exist_ok=True)
    day = today or datetime.now(timezone.utc).date().isoformat()
    target = folder / f"{PREFIX}{day}.db"
    # A half-written copy left by an earlier run that could not finish (13 October 2026: OneDrive
    # held the file open while it uploaded, and the rename failed).
    for stale in folder.glob(f"{PREFIX}*.partial"):
        try:
            stale.unlink()
        except OSError:
            pass
    # Temporary copies an earlier run could not clear (14 October 2026: 282 MB left in %TEMP%).
    for leftover in Path(tempfile.gettempdir()).glob("pu-backup-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    if target.exists():
        made = "already made today"
    else:
        # Written outside the synced folder and moved in whole. Written in place, OneDrive starts
        # uploading the half-written file and keeps it open, and the final rename is refused.
        with tempfile.TemporaryDirectory(prefix="pu-backup-", ignore_cleanup_errors=True) as scratch:
            partial = Path(scratch) / target.name
            # Closed, not only committed: `with sqlite3.connect()` commits and leaves the file open,
            # and on Windows an open file can be copied but not removed (14 October 2026: the copy
            # landed, then the temporary folder could not be cleared).
            with closing(sqlite3.connect(str(source))) as reading, \
                    closing(sqlite3.connect(str(partial))) as writing:
                reading.backup(writing)
            for attempt in range(6):
                try:
                    shutil.move(str(partial), str(target))
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(5)
        made = "made"
    copies = sorted(folder.glob(f"{PREFIX}*.db"))
    removed = []
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink()
        removed.append(old.name)
    return {"backup": made, "file": str(target), "bytes": target.stat().st_size,
            "kept": len(copies) - len(removed), "removed": removed}
