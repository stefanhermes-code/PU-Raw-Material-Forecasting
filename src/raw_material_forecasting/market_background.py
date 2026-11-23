"""The market behind the price: who makes it, how much, what is down and what is coming.

Stefan, 9 October 2026: "you have the actual signals in the application. You can use these signals
for a background story." The application has held them for weeks and the customer report has never
read them - the plant roster, the announced pipeline and the dated events that HTC Global
researched market by market.

A price paragraph says what happened. This says what the market is: eleven plants, who owns the
large ones, how concentrated that is, what capacity is under construction and when it lands, and
which units went down inside the period. A buyer reads the price to know his cost and reads this
to know whether that cost is likely to hold.

Nothing here is computed from the price series, and nothing here is opinion. It is arithmetic over
rows HTC Global wrote, and a market with no roster gets no background rather than a guess at one.
The provenance columns on those rows - who said so, and the internal note behind it - are never
read by this module at all, so they cannot reach a document through it.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

from raw_material_forecasting.market_intelligence import (DATA_DIR, capacity_file,
                                                          developments_file, pipeline_file)

# Below this a market is not usefully called concentrated, and above it a reader should be told.
CONCENTRATED = 60.0


@dataclass
class Plant:
    owner: str
    site: str
    province: str
    kt: float
    # Where the roster records a plant as a range, this is the bottom of it. The standard, 10
    # October 2026: the top of a range is never printed as a point figure.
    kt_low: float = 0.0

    @property
    def low(self) -> float:
        return self.kt_low or self.kt


@dataclass
class Coming:
    owner: str
    site: str
    kt: float
    due: str
    status: str


@dataclass
class Background:
    """What a market is made of, before anything about its price."""

    chemical: str
    geography: str
    plants: list[Plant] = field(default_factory=list)
    coming: list[Coming] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    @property
    def capacity_kt(self) -> float:
        return sum(plant.kt for plant in self.plants)

    @property
    def capacity_low_kt(self) -> float:
        return sum(plant.low for plant in self.plants)

    @property
    def capacity_is_a_range(self) -> bool:
        return round(self.capacity_low_kt) != round(self.capacity_kt)

    @property
    def owners(self) -> list[tuple[str, float]]:
        """Each owner and what they hold, largest first."""
        held: dict[str, float] = {}
        for plant in self.plants:
            held[plant.owner] = held.get(plant.owner, 0.0) + plant.kt
        return sorted(held.items(), key=lambda row: row[1], reverse=True)

    def top_share(self, count: int = 3) -> float:
        total = self.capacity_kt
        if not total:
            return 0.0
        return sum(kt for _owner, kt in self.owners[:count]) / total * 100.0

    @property
    def coming_kt(self) -> float:
        return sum(row.kt for row in self.coming)

    @property
    def has_anything(self) -> bool:
        return bool(self.plants or self.coming or self.events)


def _rows(directory: Path, name: str) -> list[dict]:
    path = Path(directory) / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle)
                if any((value or "").strip() for value in row.values())]


RANGE = re.compile(r"between\s+([\d,.]+)\s+and\s+([\d,.]+)", re.I)


def _low_of(note, kt: float) -> float:
    """The bottom of a plant recorded as a range, read out of the roster's own note."""
    found = RANGE.search(str(note or ""))
    if not found:
        return kt
    try:
        return min(float(found.group(1).replace(",", "")), kt)
    except ValueError:
        return kt


def _number(value) -> float:
    try:
        return float(str(value or "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def load(chemical: str, geography: str, *, period=None, directory: Path = DATA_DIR) -> Background:
    """The roster, the pipeline and the events of one market, with the events cut to the period."""
    out = Background(chemical=chemical, geography=geography)
    for row in _rows(directory, capacity_file(chemical, geography)):
        kt = _number(row.get("kt"))
        owner = str(row.get("owner") or "").strip()
        if not owner or kt <= 0:
            continue
        out.plants.append(Plant(owner=owner, site=str(row.get("plant") or "").strip(),
                                province=str(row.get("province") or "").strip(), kt=kt,
                                kt_low=_low_of(row.get("note"), kt)))
    for row in _rows(directory, pipeline_file(chemical, geography)):
        kt = _number(row.get("kt"))
        if kt <= 0:
            continue
        out.coming.append(Coming(owner=str(row.get("owner") or "").strip(),
                                 site=str(row.get("site") or "").strip(), kt=kt,
                                 due=str(row.get("due") or "").strip(),
                                 status=str(row.get("status") or "").strip()))
    for row in _rows(directory, developments_file(chemical, geography)):
        day = str(row.get("date") or "").strip()
        if period is not None and not period.holds(day):
            continue
        # Only the two columns a reader sees. `source` and `internal_note` are not read here.
        out.events.append({"date": day, "development": str(row.get("development") or "").strip(),
                           "type": str(row.get("type") or "").strip(),
                           "context": str(row.get("context") or "").strip()})
    out.events.sort(key=lambda row: row["date"], reverse=True)
    return out


def coming_within(background: Background, *, of: date, years: int = 3) -> list[Coming]:
    """Capacity due inside the next few years, soonest first. Anything further out is not news."""
    limit = date(of.year + years, of.month, 1).isoformat()
    inside = [row for row in background.coming if row.due and row.due <= limit]
    return sorted(inside, key=lambda row: row.due)
