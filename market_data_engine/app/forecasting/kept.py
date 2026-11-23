"""Forecasts kept on disk between starts, keyed by the evidence they were computed from.

13 October 2026: after the customs history landed, every start prepared 162 forecasts again, about
eight seconds each, twenty minutes with the gauge at "almost ready" - although nothing had changed
since the start before. The in-memory cache already keys each forecast by its evidence (number of
readings, newest date, chained history); keeping that on disk means a start only computes the
markets whose evidence moved. The forecasting code is part of the key, so a release that changes a
model recomputes everything.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

_HERE = Path(__file__).resolve().parent


# Modules the LIVE forecast never imports: the governed review (WP1/WP2) runs off the page path and
# off the engine start, so a change to it must not throw away every kept forecast and put the gauge
# back at "almost ready" for twenty minutes. Anything not listed here is stamped, so a new live module
# is covered by default and only an offline one has to be named.
OFFLINE = {"governance.py", "domain.py", "history.py", "eligibility.py", "related_series.py", "scoring.py",
           "tournament.py", "selection.py", "review_job.py", "store_series.py", "adapters"}


def code_stamp() -> str:
    """Changes whenever any forecasting module the live forecast reads changes."""
    digest = hashlib.sha1()
    for path in sorted(_HERE.rglob("*.py")):
        relative = path.relative_to(_HERE)
        if relative.parts[0] in OFFLINE or "__pycache__" in relative.parts:
            continue
        digest.update(str(relative).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


_STAMP = code_stamp()


def folder_for(database_url: str) -> Path | None:
    stated = os.environ.get("FORECAST_CACHE_DIR", "").strip()
    if stated:
        return Path(stated)
    if database_url.startswith("sqlite") and ":///" in database_url:
        db = Path(database_url.split(":///", 1)[1])
        return db.parent / "forecast_cache"
    return None


def _file(folder: Path, identity_key: str) -> Path:
    return folder / (hashlib.sha1(identity_key.encode("utf-8")).hexdigest() + ".json")


def _key(signature: tuple) -> list:
    return [_STAMP, *json.loads(json.dumps(jsonable_encoder(list(signature))))]


def load(folder: Path | None, identity_key: str, signature: tuple) -> dict | None:
    if folder is None:
        return None
    try:
        held = json.loads(_file(folder, identity_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if held.get("signature") != _key(signature):
        return None
    payload = held.get("payload")
    return payload if isinstance(payload, dict) else None


def save(folder: Path | None, identity_key: str, signature: tuple, payload: dict[str, Any]) -> None:
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = _file(folder, identity_key)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"identity_key": identity_key, "signature": _key(signature),
                                         "payload": jsonable_encoder(payload)}),
                             encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        pass   # a forecast that cannot be kept is still served
