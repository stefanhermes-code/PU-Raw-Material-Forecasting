"""The photographs the report is allowed to use, and which one this edition gets.

V2.1 asks for photography in the publication and for it to vary between editions. Two rules hold
here and neither is negotiable. A photograph is used only when it has been approved in the
register, because a picture in a customer's hands is a claim about what HTC Global does. And the
register records where each one came from and who holds the rights, so that claim can be checked
later by someone who was not in the room.

The register is data, not code: `data/config/image_register.json`. Adding a photograph is adding
a file and a row, which is a thing Stefan can do without me.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REGISTER = Path("data/config/image_register.json")


def entries(register: Path | None = None) -> list[dict[str, Any]]:
    """Every row in the register, approved or not."""
    path = Path(register or REGISTER)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = held.get("images") if isinstance(held, dict) else held
    return [dict(one) for one in (rows or []) if isinstance(one, dict)]


def approved(slot: str, register: Path | None = None) -> list[dict[str, Any]]:
    """The approved photographs for one slot, in the order the register lists them."""
    return [one for one in entries(register)
            if str(one.get("slot") or "") == slot and bool(one.get("approved"))]


def _chosen(rows: list[dict[str, Any]], edition: str) -> dict[str, Any] | None:
    """Which of the approved ones this edition gets.

    Chosen from the edition's own name rather than at random, so the same edition built twice is
    the same document, and two editions in a row are not the same picture.
    """
    if not rows:
        return None
    return rows[sum(ord(letter) for letter in str(edition)) % len(rows)]


def photographs(edition: str, *, root: Path | None = None,
                register: Path | None = None) -> dict[str, bytes]:
    """The picture for each slot this edition can fill, read from disk.

    A slot with no approved photograph is absent, and the report draws nothing there rather than
    leaving a gap where a picture was meant to be.
    """
    base = Path(root or ".")
    out: dict[str, bytes] = {}
    for slot in ("banner", "band"):
        row = _chosen(approved(slot, register), edition)
        if not row:
            continue
        where = base / str(row.get("file") or "")
        try:
            out[slot] = where.read_bytes()
        except OSError:
            continue
    return out


def described(edition: str, register: Path | None = None) -> list[str]:
    """What this edition shows, for the record rather than for the page."""
    out = []
    for slot in ("banner", "band"):
        row = _chosen(approved(slot, register), edition)
        if row:
            out.append(f"{slot}: {row.get('subject')} ({row.get('source')})")
    return out
