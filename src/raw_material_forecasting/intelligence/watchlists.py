from __future__ import annotations
import json
from pathlib import Path


def load_watchlist(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("queries"):
        raise ValueError("watchlist requires at least one query")
    return cfg
