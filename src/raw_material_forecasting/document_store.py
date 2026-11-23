"""Every document that was imported, kept, so a reader can be improved without asking for it again.

`document_intake` opens with this promise:

    "A file is always kept. Whether anything could be read out of it or not, the bytes are stored
    as evidence, and a format we cannot read today becomes readable later without asking Stefan to
    find the document again."

It was never built. `document_intake.read(name, data)` took the bytes, returned text and tables,
and stored nothing; the register kept a filename in a column and nothing else. What that cost was
visible on 16 October 2026: the 12 October briefing had been imported and yielded one item out of
eight, and the other seven were unrecoverable from inside the application even though the Word file
was still sitting in OneDrive.

Stefan, on being asked why the import turns documents into rows at all: the index is fair enough,
throwing the document away is not.

So documents are content-addressed, as the engine's own collected evidence already is: the SHA-256
of the bytes, sharded two characters deep. The same document imported twice is one file with two
sightings recorded against it, which is also how a re-import after a reader improves is told apart
from a new arrival. Nothing here reads a document or decides what it means.

Next to the engine's data so the application and the engine see one store:
%LOCALAPPDATA%/HTCGlobal/PUForecast/documents, or the path in PUFORECAST_DOCUMENTS.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX = "documents.jsonl"


@dataclass
class Kept:
    """One document held in the store, and every time it has arrived."""

    sha256: str
    size: int
    names: tuple[str, ...] = ()        # what it has been called; a file can be renamed in a folder
    kind: str = ""                     # briefing / condition / monitor / newsletter / prices
    folder: str = ""                   # the watched folder it came from, where it came from one
    first_seen: str = ""
    last_seen: str = ""
    sightings: int = 1
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        """The name it arrived with most recently, which is the one to show."""
        return self.names[-1] if self.names else ""


def root() -> Path:
    stated = os.environ.get("PUFORECAST_DOCUMENTS", "").strip()
    if stated:
        return Path(stated)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "HTCGlobal" / "PUForecast" / "documents"
    return Path.home() / ".htcglobal" / "puforecast" / "documents"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _where(sha: str, *, where: Path | None = None) -> Path:
    """Sharded two characters deep, the same shape the engine stores raw evidence in."""
    return (where or root()) / sha[:2] / sha


def index_path(where: Path | None = None) -> Path:
    return (where or root()) / INDEX


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_index(where: Path | None = None) -> dict[str, Kept]:
    path = index_path(where)
    held: dict[str, Kept] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return held
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # One unreadable line is not a reason to lose the rest of the index.
            continue
        sha = _text(row.get("sha256"))
        if not sha:
            continue
        held[sha] = Kept(
            sha256=sha,
            size=int(row.get("size") or 0),
            names=tuple(str(n) for n in (row.get("names") or ()) if str(n).strip()),
            kind=_text(row.get("kind")),
            folder=_text(row.get("folder")),
            first_seen=_text(row.get("first_seen")),
            last_seen=_text(row.get("last_seen")),
            sightings=int(row.get("sightings") or 1),
            notes=tuple(str(n) for n in (row.get("notes") or ())),
        )
    return held


def _write_index(held: dict[str, Kept], where: Path | None = None) -> None:
    path = index_path(where)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({
        "sha256": item.sha256, "size": item.size, "names": list(item.names), "kind": item.kind,
        "folder": item.folder, "first_seen": item.first_seen, "last_seen": item.last_seen,
        "sightings": item.sightings, "notes": list(item.notes),
    }, ensure_ascii=False) + "\n" for item in sorted(held.values(), key=lambda k: k.first_seen))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


def keep(name: str, data: bytes, *, kind: str = "", folder: str = "", notes=(),
         where: Path | None = None) -> Kept:
    """Hold these bytes and record the sighting. Returns what is now held.

    Called before anything tries to read the document, so a file we cannot read today is kept all
    the same - which is the whole point of the promise this module exists to deliver.
    """
    sha = digest(data)
    target = _where(sha, where=where)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    held = _read_index(where)
    before = held.get(sha)
    if before is None:
        entry = Kept(sha256=sha, size=len(data), names=(_text(name),) if _text(name) else (),
                     kind=_text(kind), folder=_text(folder), first_seen=now, last_seen=now,
                     sightings=1, notes=tuple(str(n) for n in notes))
        held[sha] = entry
    else:
        names = before.names
        if _text(name) and _text(name) not in names:
            names = names + (_text(name),)
        entry = Kept(sha256=sha, size=before.size or len(data), names=names,
                     kind=_text(kind) or before.kind, folder=_text(folder) or before.folder,
                     first_seen=before.first_seen or now, last_seen=now,
                     sightings=before.sightings + 1,
                     notes=tuple(dict.fromkeys(before.notes + tuple(str(n) for n in notes))))
        held[sha] = entry
    _write_index(held, where)
    return entry


def held(where: Path | None = None) -> list[Kept]:
    """Everything in the store, oldest arrival first."""
    return sorted(_read_index(where).values(), key=lambda k: (k.first_seen, k.name))


def bytes_of(sha: str, where: Path | None = None) -> bytes | None:
    """The document itself, or None where the index knows it and the file has gone."""
    try:
        return _where(_text(sha), where=where).read_bytes()
    except OSError:
        return None


def legacy_by_name(name: str, where: Path | None = None) -> Kept | None:
    """Find a held document by the file name it arrived under. A BRIDGE, not an identity.

    Named to be unmistakable at the call site. A document's identity here is the SHA-256 of its
    bytes, because that is the one thing nobody can rename out from under us. Stefan, 16 October
    2026: "You are judging by report title....not by content.....that is dangerous." He is right,
    and the danger is concrete - the same weekly report has arrived as "2026-09-30 Strait of Hormuz
    PU Supply Chain Special Report.docx" and "2026-10-05 - Strait of Hormuz PU Supply Chain Special
    Report.docx", with a hyphen appearing between one week and the next, and the same series is
    called the PU Supply Chain Geopolitical Update elsewhere.

    This exists only for records written before anything kept the document, which hold a file name
    and nothing else. Nothing new should reach for it.
    """
    wanted = _text(name)
    matches = [item for item in _read_index(where).values() if wanted in item.names]
    return max(matches, key=lambda k: k.last_seen) if matches else None
