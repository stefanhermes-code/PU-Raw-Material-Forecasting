"""What a document is, decided by reading it, and remembered so it is read once.

Stefan, 26 October 2026: *"I told you before that the file title is not decisive, neither is the
saving date. I dont know why you not understand that you need to explore the content."*

The waiting list still decided what had been read by comparing file names. That is why five reports
already in the store sat on it as though untouched - the same report arrives as
"2026-07-03_Strait_of_Hormuz..." and "2026-07-03 - Strait of Hormuz...", and one week arrived twice
as "APBA_Week_30_2026.html" and "APBA_Week_30_2026 (1).html".

A document's identity here is two things it tells you itself:

- its **content hash**, which makes the same bytes the same document whatever it is called; and
- the **day it states it was written**, which makes the Word and the PDF of one report one report,
  because they say the same day even though their bytes differ.

Reading 102 documents takes a minute, so what was read is remembered: the hash and the day are
cached against the file's size and modification time, and a file is only opened again when one of
those changes. A read log then records what has actually been stored, by hash and by (folder, day),
so the list can be right without opening anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE = "documents_read.json"


@dataclass(frozen=True)
class Identity:
    """One document, as the document describes itself."""

    sha: str
    day: str                      # the day it states it was written, or "" where it states none
    name: str = ""
    # How much readable text came out. Where one report arrives twice - "APBA_Week_30_2026.html"
    # at 8 KB and "APBA_Week_30_2026 (1).html" at 2.5 MB - this is how the fuller copy is chosen,
    # which is a question about the documents and not about their names.
    chars: int = 0

    @property
    def dated(self) -> bool:
        return bool(self.day)


def path(directory: Path) -> Path:
    return Path(directory) / FILE


def load(directory: Path) -> dict[str, Any]:
    """The cache and the read log, or empty ones."""
    place = path(directory)
    if not place.exists():
        return {"cache": {}, "read": []}
    try:
        held = json.loads(place.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"cache": {}, "read": []}
    held.setdefault("cache", {})
    held.setdefault("read", [])
    return held


def save(held: dict[str, Any], directory: Path) -> None:
    place = path(directory)
    place.parent.mkdir(parents=True, exist_ok=True)
    place.write_text(json.dumps(held, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _key(file: Path) -> str:
    """A file as the cache knows it: where it is, how big, and when it last changed."""
    try:
        stat = file.stat()
        return f"{file}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        return str(file)


def identity_of(file: Path, held: dict[str, Any], *, read_day: Callable[[Any], str],
                open_document: Callable[[str, bytes], Any]) -> Identity:
    """What this document is, from the cache where it is unchanged and from the file where it is not.

    `read_day` and `open_document` are passed in rather than imported so this module does not drag
    the document readers into every caller, and so a test can say what a document says without
    writing a Word file.
    """
    key = _key(file)
    remembered = held["cache"].get(key)
    if remembered:
        return Identity(sha=remembered.get("sha", ""), day=remembered.get("day", ""),
                        name=file.name, chars=int(remembered.get("chars", 0)))
    try:
        data = file.read_bytes()
    except OSError:
        return Identity(sha="", day="", name=file.name)
    sha = hashlib.sha256(data).hexdigest()
    day, chars = "", 0
    try:
        document = open_document(file.name, data)
        day = read_day(document) or ""
        chars = len(str(getattr(document, "text", "") or ""))
    except Exception:
        day, chars = "", 0
    held["cache"][key] = {"sha": sha, "day": day, "chars": chars}
    return Identity(sha=sha, day=day, name=file.name, chars=chars)


def already_read(identity: Identity, folder: str, held: dict[str, Any]) -> bool:
    """Whether this document's content has been stored, whatever the file is called.

    Two ways it can be: the same bytes have been read before, or something from the same folder
    was stored for the same day. The second is what makes the Word and the PDF of one report one
    report - their bytes differ and their day does not.
    """
    for row in held["read"]:
        if identity.sha and row.get("sha") == identity.sha:
            return True
        if identity.dated and row.get("folder") == folder and row.get("day") == identity.day:
            return True
    return False


def record(identity: Identity, folder: str, held: dict[str, Any], *,
           when: date | None = None) -> None:
    """Remember that this document's content was stored."""
    if already_read(identity, folder, held):
        return
    held["read"].append({
        "sha": identity.sha,
        "day": identity.day,
        "folder": folder,
        "name": identity.name,
        "read_on": (when or datetime.now(timezone.utc).date()).isoformat(),
    })
