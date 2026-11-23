"""Which release is running, and which one is installed.

Python imports a package once, when the process starts. Editing a file under `src/` changes nothing
until that process restarts. The Platform Admin caption read VERSION from disk on every rerun, so it
announced a new release while the old modules were still answering - a fixed defect looked unfixed,
and the caption was the reason nobody suspected a stale process.

`running_version()` is captured at import, so it is the code that is actually answering.
`version_on_disk()` is read each time. When they differ, the application says so.
"""
from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path("VERSION")
UNKNOWN = "unknown"


def _read() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or UNKNOWN
    except OSError:
        return UNKNOWN


# Captured once, at import: the release this process loaded.
_RUNNING = _read()


def running_version() -> str:
    """The release this Python process started with."""
    return _RUNNING


def version_on_disk() -> str:
    """The release sitting in the working directory right now."""
    return _read()


def restart_required() -> bool:
    """Whether a newer release is installed than the one answering."""
    on_disk = version_on_disk()
    return on_disk != UNKNOWN and _RUNNING != UNKNOWN and on_disk != _RUNNING


def restart_message() -> str:
    """Said when the release on disk is newer than the one answering.

    The launcher used to be named per release and this message named the file to click, so the file
    name carried the signal. There is one `Run-MVP.bat` now: five identical copies told a reader
    nothing the version comparison above does not already say, and the one thing the naming bought -
    knowing whether you had restarted - is exactly what this message is for.
    """
    return (
        f"Version {version_on_disk()} is installed but v{running_version()} is still running. "
        "Close the application window and start Run-MVP.bat to load it. "
        "Refreshing the browser is not enough."
    )
