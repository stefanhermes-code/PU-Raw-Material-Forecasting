"""Check a reference pack before it reaches the store.

The importer is deliberately forgiving: a row whose date or value will not parse is skipped so that
one bad line cannot cost a whole pack. The cost of that is silence - a pack half of whose rows were
dropped still imports as "ok", with a lower row count that nobody has anything to compare against.
This is the report that makes the difference visible, and it runs before the import rather than after.

It also looks for the two ways a gathered dataset gets filled in rather than found: a value repeated
across consecutive periods where the publisher had nothing, and a run whose differences are exactly
constant, which is a straight line drawn between two real points. Neither is proof - a genuinely flat
market repeats, and two months in a row can move by the same amount - so both are reported as
something to look at, with the dates, never as a verdict.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from app.paths import repo_path
from app.reference.importer import (
    QUALITY_TIERS, REQUIRED_COLUMNS, _as_date, is_price_unit, read_long_csv, series_id,
)

ROUTES_FILE = repo_path("data", "config", "chemical_routes_v1_1.json")

# How long a run has to be before it is worth a second look. Three identical monthly prints happen in
# a quiet market; six is unusual enough to ask about.
REPEAT_RUN = 6
STRAIGHT_RUN = 5

# An index has to say what it is based on, or a reader can take it for money per tonne.
_INDEX_HINTS = ("index", "=100", "base")


def route_codes(path: Path | str = ROUTES_FILE) -> set[str]:
    """The chemical codes the correlation engine can match on. Empty when the seed is missing, which
    turns the unknown-code check off rather than failing every row against nothing."""
    try:
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(node.get("code") or node.get("id") or "").strip().upper()
            for node in seed.get("nodes", [])} - {""}


def _finding(level: str, check: str, detail: str, rows: Sequence = ()) -> dict:
    return {"level": level, "check": check, "detail": detail, "rows": list(rows)[:12]}


def _runs(values: Sequence[float], days: Sequence[date]) -> tuple[list, list]:
    """Consecutive repeats, and consecutive constant first differences."""
    repeats, straights = [], []
    start = 0
    for index in range(1, len(values) + 1):
        if index < len(values) and values[index] == values[start]:
            continue
        if index - start >= REPEAT_RUN:
            repeats.append((days[start].isoformat(), days[index - 1].isoformat(), values[start], index - start))
        start = index

    if len(values) >= STRAIGHT_RUN:
        diffs = [round(b - a, 10) for a, b in zip(values, values[1:])]
        start = 0
        for index in range(1, len(diffs) + 1):
            if index < len(diffs) and diffs[index] == diffs[start] and diffs[start] != 0:
                continue
            if index - start + 1 >= STRAIGHT_RUN:
                straights.append((days[start].isoformat(), days[index].isoformat(), diffs[start], index - start + 1))
            start = index
    return repeats, straights


def validate_rows(rows: Iterable[dict], *, codes: set[str] | None = None) -> dict:
    """Findings plus a per-series summary. Errors block an import; warnings are for the reader."""
    rows = list(rows)
    known = route_codes() if codes is None else codes
    findings: list[dict] = []

    if not rows:
        return {"rows": 0, "series": [], "errors": 1, "warnings": 0,
                "findings": [_finding("error", "empty", "No row carried all of "
                                      + ", ".join(REQUIRED_COLUMNS))]}

    unknown, bad_date, bad_value = [], [], []
    blank_geography, blank_url, blank_series, bad_quality, blank_unit, silent_index = [], [], [], [], [], []
    points: dict[str, dict] = {}

    for line, row in enumerate(rows, start=2):     # header is line 1
        chemical = str(row.get("chemical") or "").strip().upper().replace(" ", "_")
        day = _as_date(row.get("date"))
        try:
            value = float(str(row.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            value = None

        if known and chemical not in known:
            unknown.append(f"line {line}: {chemical or '(blank)'}")
        if day is None:
            bad_date.append(f"line {line}: {row.get('date')!r}")
        if value is None:
            bad_value.append(f"line {line}: {row.get('value')!r}")

        geography = str(row.get("geography") or "").strip()
        label = str(row.get("series") or "").strip()
        unit = str(row.get("unit") or "").strip()
        quality = str(row.get("quality") or "").strip().upper().replace(" ", "_").replace("-", "_")

        if not geography:
            blank_geography.append(f"line {line}")
        if not str(row.get("source_url") or "").strip():
            blank_url.append(f"line {line}")
        if not label:
            blank_series.append(f"line {line}")
        if quality and quality not in QUALITY_TIERS:
            bad_quality.append(f"line {line}: {row.get('quality')!r}")
        if not unit:
            blank_unit.append(f"line {line}")
        elif not is_price_unit(unit) and not any(hint in unit.lower() for hint in _INDEX_HINTS):
            silent_index.append(f"line {line}: {unit!r}")

        if day is None or value is None:
            continue
        key = series_id(chemical, geography or "UNKNOWN", label or chemical)
        held = points.setdefault(key, {"chemical": chemical, "geography": geography, "label": label,
                                       "unit": unit, "quality": quality, "by_date": {}, "clashes": []})
        if day in held["by_date"] and held["by_date"][day] != value:
            held["clashes"].append(f"{day.isoformat()}: {held['by_date'][day]} and {value}")
        held["by_date"][day] = value

    if unknown:
        findings.append(_finding("error", "unknown chemical",
                                 f"{len(unknown)} rows name a code that is not in the route graph, so they "
                                 "can never be matched to a chain", unknown))
    if bad_date:
        findings.append(_finding("error", "unreadable date",
                                 f"{len(bad_date)} rows would be dropped silently by the importer", bad_date))
    if bad_value:
        findings.append(_finding("error", "unreadable value",
                                 f"{len(bad_value)} rows would be dropped silently by the importer", bad_value))
    if blank_series:
        findings.append(_finding("error", "no series identifier",
                                 f"{len(blank_series)} rows carry no publisher identifier, so a later "
                                 "extension of the same series would import as a second series", blank_series))
    if blank_unit:
        findings.append(_finding("error", "no unit",
                                 f"{len(blank_unit)} rows carry no unit, and the unit is what decides "
                                 "whether a series may ever be shown as a price", blank_unit))
    if silent_index:
        findings.append(_finding("warning", "index without a base",
                                 f"{len(silent_index)} rows have a unit that is neither a price nor a "
                                 "stated index base; an index read as money per tonne is the confusion "
                                 "the reference lane exists to prevent", silent_index))
    if bad_quality:
        findings.append(_finding("warning", "quality tier not recognised",
                                 f"{len(bad_quality)} rows would be imported as USABLE_PROXY by default, "
                                 "which may overstate them", bad_quality))
    if blank_geography:
        findings.append(_finding("warning", "no geography",
                                 f"{len(blank_geography)} rows would be stored as UNKNOWN; a US index and a "
                                 "Chinese price are not the same evidence", blank_geography))
    if blank_url:
        findings.append(_finding("warning", "no source url",
                                 f"{len(blank_url)} rows cannot be traced back to the page they came from",
                                 blank_url))

    # The same publisher series mapped onto two chemicals. It is a legitimate way to use a group
    # index, and the engine must still never read it as two pieces of evidence: the correlation
    # between the two chemicals is exactly 1.0 by construction, and each would report the identical
    # coefficient against the same target under a different name.
    by_label: dict[str, set[str]] = {}
    for held in points.values():
        by_label.setdefault(held["label"].strip().upper(), set()).add(held["chemical"])
    shared = {label: sorted(chemicals) for label, chemicals in by_label.items() if len(chemicals) > 1}
    for label, chemicals in shared.items():
        findings.append(_finding("warning", "one series under two chemicals",
                                 f"{' and '.join(chemicals)} carry the same publisher series, so they "
                                 "are one signal wearing two names, not two independent histories",
                                 [f"{label}: {', '.join(chemicals)}"]))

    summary = []
    for key, held in sorted(points.items(), key=lambda item: (item[1]["chemical"], item[1]["label"])):
        days = sorted(held["by_date"])
        values = [held["by_date"][day] for day in days]
        repeats, straights = _runs(values, days)
        if held["clashes"]:
            findings.append(_finding("error", "two values for one date",
                                     f"{held['chemical']} - {held['label']}: the same series and date "
                                     "carry different numbers", held["clashes"]))
        if repeats:
            findings.append(_finding("warning", "value repeated across a run",
                                     f"{held['chemical']} - {held['label']}: {len(repeats)} run(s) of "
                                     f"{REPEAT_RUN} or more identical values. A flat market does this; so "
                                     "does carrying the last known value forward",
                                     [f"{a} to {b}: {v} ({n} periods)" for a, b, v, n in repeats]))
        if straights:
            findings.append(_finding("warning", "exactly constant differences",
                                     f"{held['chemical']} - {held['label']}: {len(straights)} run(s) of "
                                     f"{STRAIGHT_RUN} or more points moving by an identical step, which is "
                                     "what a straight line drawn between two real points looks like",
                                     [f"{a} to {b}: step {d} ({n} points)" for a, b, d, n in straights]))
        summary.append({
            "series_id": key, "chemical": held["chemical"], "label": held["label"],
            "geography": held["geography"], "unit": held["unit"],
            "quality": held["quality"] or "USABLE_PROXY (default)",
            "is_index": not is_price_unit(held["unit"]),
            "shares_series_with": [c for c in shared.get(held["label"].strip().upper(), [])
                                  if c != held["chemical"]],
            "points": len(days),
            "from": days[0].isoformat() if days else "",
            "to": days[-1].isoformat() if days else "",
        })

    return {
        "rows": len(rows),
        "series": summary,
        "errors": len([f for f in findings if f["level"] == "error"]),
        "warnings": len([f for f in findings if f["level"] == "warning"]),
        "findings": findings,
    }


def validate_pack(path: Path | str) -> dict:
    report = validate_rows(read_long_csv(path))
    report["pack"] = Path(path).name
    return report
