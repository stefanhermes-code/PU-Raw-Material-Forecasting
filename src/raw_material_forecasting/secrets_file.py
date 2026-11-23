"""The keys the application needs, kept where a repository cannot carry them away.

Stefan, 5 October 2026, agreed the document import may use the OpenAI key PU ExpertCenter already
has. Pointing this application at that application's `.env` would tie the two together - one of them
moves its folder or rotates its key and the other breaks without saying why - so the key is copied
once into this application's own file and each owns its copy.

The file lives beside the store, under the Windows application-data folder the launcher already
uses, and never inside the project folder: that folder is synced to OneDrive, and a key in it would
be a key in the cloud and in every backup.

Nothing here logs, prints or returns a value. It loads names into the environment and reports which
names it found, so a page can say "the key is not set" without ever being able to show it.
"""
from __future__ import annotations

import os
from pathlib import Path

FILE_NAME = "secrets.env"


def candidate_paths() -> list[Path]:
    """Where the file may be, most explicit first."""
    out: list[Path] = []
    stated = os.environ.get("PUFORECAST_SECRETS", "").strip()
    if stated:
        out.append(Path(stated))
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        out.append(Path(local) / "HTCGlobal" / "PUForecast" / FILE_NAME)
    out.append(Path.home() / ".htcglobal" / "puforecast" / FILE_NAME)
    return out


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load() -> list[str]:
    """Put the stored keys into the environment. Returns the NAMES found, never the values.

    An existing environment variable wins, so a shell that already set a key for one run is not
    overridden by the file.
    """
    for path in candidate_paths():
        try:
            if not path.is_file():
                continue
            values = parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for name, value in values.items():
            if value and not os.environ.get(name):
                os.environ[name] = value
        return sorted(values)
    return []
