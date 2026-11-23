"""Keeping the editions we issue, so a later one can refer to what we said before.

Stefan, 9 October 2026: "every report we make here should be uploaded back into the vectorstore so
we build a reference base."

Two rules make that safe, and both are enforced here rather than described.

The first is that an edition enters only after it has passed its gates. A document carrying a
failed check is refused, because an error uploaded once becomes retrievable history and the next
edition can restate it in good faith.

The second is that a retrieved edition is a record of what HTC Global stated on a date, and never
a source of a number. The fact pack goes up beside its edition so a continuity claim can be
checked against figures instead of against prose, and the attributes carry enough to filter a
search down to one market, one period and one version.

An edition is immutable. A correction goes up as a new version and the one it replaces is marked,
so retrieval can leave it behind.
"""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from raw_material_forecasting.document_reader import api_key, configured as key_held

API = "https://api.openai.com/v1"
PROJECT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT / "data" / "config" / "reference_base.json"
TIMEOUT = 60


class ArchiveUnavailable(RuntimeError):
    """No key, or no store named yet."""


class Refused(RuntimeError):
    """The edition did not meet the standard, so it does not go into the base."""


@dataclass
class Receipt:
    uploaded: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    store: str = ""

    @property
    def done(self) -> bool:
        return bool(self.uploaded)


def settings(path: Path | None = None) -> dict[str, Any]:
    where = Path(path) if path else CONFIG
    try:
        return json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store_id(path: Path | None = None) -> str:
    return str(settings(path).get("vector_store_id", "") or "").strip()


def configured(path: Path | None = None) -> bool:
    """A key and a store. Until Charlie names the store this stays false and nothing is sent."""
    return bool(key_held() and store_id(path))


def _request(method: str, url: str, *, body: bytes | None = None,
             content_type: str = "application/json") -> dict[str, Any]:
    key = api_key()
    if not key:
        raise ArchiveUnavailable("OPENAI_API_KEY is not set in the engine's environment.")
    headers = {"Authorization": f"Bearer {key}"}
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.loads(answer.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as problem:
        detail = problem.read().decode("utf-8", "ignore")[:300]
        raise ArchiveUnavailable(f"{problem.code} from the reference base: {detail}") from None
    except urllib.error.URLError as problem:
        raise ArchiveUnavailable(f"the reference base could not be reached: {problem}") from None


def _multipart(name: str, payload: bytes, purpose: str = "assistants") -> tuple[bytes, str]:
    edge = "----PI3ReferenceBase7f2a"
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    parts = [
        f"--{edge}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n"
        .encode("utf-8"),
        f"--{edge}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
        f"Content-Type: {kind}\r\n\r\n".encode("utf-8"),
        payload, b"\r\n", f"--{edge}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={edge}"


def _slug(text: str) -> str:
    keep = [character if character.isalnum() else "-" for character in str(text)]
    return "".join(keep).strip("-").replace("--", "-").lower()


def _field(row: Any, name: str) -> str:
    """A row is either a MarketPeriod or the light record the screen kept of one."""
    if isinstance(row, dict):
        return str(row.get(name, "") or "")
    return str(getattr(row, name, "") or "")


def attributes_for(markets: Sequence, *, period_name: str, period_to: str, currency: str,
                   version: int, kind: str, standard: str) -> dict[str, Any]:
    """What a search can filter on. Sixteen keys is the ceiling, so each one earns its place."""
    chemicals = sorted({_field(row, "chemical") for row in markets if row is not None})
    regions = sorted({_field(row, "geography") for row in markets if row is not None})
    return {
        "kind": kind,
        "chemical": ", ".join(name for name in chemicals if name)[:120],
        "geography": ", ".join(name for name in regions if name)[:120],
        "period": period_name,
        "period_to": period_to,
        "currency": currency,
        "version": version,
        "standard": standard,
        "superseded": False,
        "publisher": "HTC Global",
    }


def existing(store: str) -> list[dict[str, Any]]:
    answer = _request("GET", f"{API}/vector_stores/{store}/files?limit=100")
    return list(answer.get("data") or [])


def _matching(files: Iterable[dict[str, Any]], attributes: dict[str, Any]) -> list[dict[str, Any]]:
    """Earlier files for the same market, period and kind, still standing."""
    out = []
    for one in files:
        held = one.get("attributes") or {}
        if held.get("superseded"):
            continue
        same = all(held.get(key) == attributes.get(key)
                   for key in ("kind", "chemical", "geography", "period"))
        if same:
            out.append(one)
    return out


def next_version(store: str, attributes: dict[str, Any]) -> int:
    highest = 0
    for one in existing(store):
        held = one.get("attributes") or {}
        if all(held.get(key) == attributes.get(key)
               for key in ("kind", "chemical", "geography", "period")):
            try:
                highest = max(highest, int(held.get("version") or 0))
            except (TypeError, ValueError):
                continue
    return highest + 1


def _upload_one(store: str, name: str, payload: bytes, attributes: dict[str, Any]) -> str:
    body, content_type = _multipart(name, payload)
    uploaded = _request("POST", f"{API}/files", body=body, content_type=content_type)
    file_id = str(uploaded.get("id") or "")
    if not file_id:
        raise ArchiveUnavailable("the reference base returned no file id.")
    _request("POST", f"{API}/vector_stores/{store}/files",
             body=json.dumps({"file_id": file_id, "attributes": attributes}).encode("utf-8"))
    return file_id


def _mark_superseded(store: str, files: Sequence[dict[str, Any]]) -> list[str]:
    marked = []
    for one in files:
        held = dict(one.get("attributes") or {})
        held["superseded"] = True
        _request("POST", f"{API}/vector_stores/{store}/files/{one.get('id')}",
                 body=json.dumps({"attributes": held}).encode("utf-8"))
        marked.append(str(one.get("id")))
    return marked


def put(*, edition_text: str, facts: dict[str, Any], markets: Sequence, period_name: str,
        period_to: str, currency: str, checks: Sequence[str], standard: str,
        path: Path | None = None) -> Receipt:
    """Send one issued edition and its fact pack to the reference base.

    A document with an outstanding check never goes up. Stefan, 9 October 2026, on the two sample
    editions: both carried a twenty-six day move labelled as a quarter, and uploading that would
    have made the sentence retrievable history.
    """
    if checks:
        raise Refused(f"{len(checks)} check(s) outstanding, so the edition stays out of the base.")
    store = store_id(path)
    if not store:
        raise ArchiveUnavailable("no reference base is named yet in data/config/reference_base.json.")
    if not key_held():
        raise ArchiveUnavailable("OPENAI_API_KEY is not set in the engine's environment.")

    shape = attributes_for(markets, period_name=period_name, period_to=period_to,
                           currency=currency, version=1, kind="edition", standard=standard)
    version = next_version(store, shape)
    stem = _slug(f"{shape['chemical']}-{shape['geography']}-{shape['period']}-v{version}")

    receipt = Receipt(store=store)
    held = existing(store)
    for kind, name, payload in (
            ("edition", f"{stem}.txt", edition_text.encode("utf-8")),
            ("facts", f"{stem}-facts.json",
             json.dumps(facts, indent=2, sort_keys=True, default=str).encode("utf-8"))):
        attributes = attributes_for(markets, period_name=period_name, period_to=period_to,
                                    currency=currency, version=version, kind=kind,
                                    standard=standard)
        receipt.superseded += _mark_superseded(store, _matching(held, attributes))
        receipt.uploaded.append(_upload_one(store, name, payload, attributes))
    return receipt
