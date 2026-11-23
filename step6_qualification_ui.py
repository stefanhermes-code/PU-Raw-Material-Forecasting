from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_qualification_report(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def qualification_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in payload.get("sources", []):
        attempts = source.get("attempts") or []
        latest = attempts[-1] if attempts else {}
        rows.append(
            {
                "Source": source.get("source_id", "-"),
                "Status": "GREEN" if source.get("green") is True else "NOT GREEN",
                "Last state": latest.get("run_state", "NOT RUN"),
                "Fresh": "YES" if latest.get("freshness_ok") is True else "NO",
                "Repeatable": "YES" if source.get("repeatability_ok") is True else "NO",
                "Parsed": latest.get("parsed_count", 0),
                "Persisted": latest.get("persisted_count", 0),
                "Read-back": latest.get("readback_count", 0),
                "Error": latest.get("error") or "-",
            }
        )
    return rows


def qualification_progress(payload: dict[str, Any]) -> dict[str, Any]:
    total = int(payload.get("total_sources") or 13)
    completed = int(payload.get("completed_sources") or len(payload.get("sources") or []))
    green = int(payload.get("green_sources") or 0)
    return {
        "completed": completed,
        "total": total,
        "green": green,
        "closed": bool(payload.get("closed")),
    }


def qualification_frontpage_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "label": "QUALIFICATION PENDING",
            "completed": 0,
            "total": 13,
            "green": 0,
            "closed": False,
        }
    progress = qualification_progress(payload)
    return {
        "label": f"{progress['green']}/{progress['total']} GREEN",
        "completed": progress["completed"],
        "total": progress["total"],
        "green": progress["green"],
        "closed": progress["closed"] and progress["green"] == progress["total"],
    }
