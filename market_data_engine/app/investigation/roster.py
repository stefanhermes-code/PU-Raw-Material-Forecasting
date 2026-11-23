"""W2, answered from our own map: which plants could this be?

Stefan, 17 October 2026: "If it is unattributed you do not throw away the observation. You dig
deeper, for example if there are 4 companies with TDI in Northwest then you check their websites."
And: "how many northwest tdi plants are there?"

Our own roster answers it. Northwest China holds three TDI plants - Wanhua Xinjiang, Gansu Yinguang
Juyin and Lanzhou Petrochemical - so a case about "a certain Northwest TDI plant" opens with three
named suspects and their capacities instead of a blank. That is not a resolution and it is not
nothing: it is attributable, falsifiable, and publishable as what it is.

The rosters are the capacity files the Market Intelligence page already maintains, read by path so
the engine and the application use one copy. They cover TDI, polymeric MDI and polyether polyol in
China, Europe and North America - 90 plants - and nothing else yet. A gap the map cannot reach is
reported as such rather than guessed at, which is what makes the chain's fifth question honest.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from app.paths import repo_path

ROSTERS = ("data", "market_intelligence")

# The sub-regions a Chinese publisher names, as the provinces our roster records. Taken from the
# standard statistical regions, which is what a market report means by the word.
_CHINA_REGIONS: dict[str, tuple[str, ...]] = {
    "northwest": ("Xinjiang", "Gansu", "Qinghai", "Ningxia", "Shaanxi"),
    "northeast": ("Liaoning", "Jilin", "Heilongjiang"),
    "north": ("Hebei", "Shanxi", "Beijing", "Tianjin", "Inner Mongolia"),
    "east": ("Shandong", "Jiangsu", "Zhejiang", "Anhui", "Fujian", "Jiangxi", "Shanghai"),
    "south": ("Guangdong", "Guangxi", "Hainan", "Fujian"),
    "central": ("Henan", "Hubei", "Hunan"),
    "southwest": ("Sichuan", "Chongqing", "Guizhou", "Yunnan", "Tibet"),
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def roster_path(chemical: str, geography: str) -> Path:
    return repo_path(*ROSTERS, f"{_slug(chemical)}_{_slug(geography)}_capacity.csv")


def have_a_map(chemical: str, geography: str) -> bool:
    """Whether the map reaches this market at all. Nine of about forty, as of 18 October 2026."""
    return roster_path(chemical, geography).exists()


def plants(chemical: str, geography: str) -> list[dict]:
    """Every plant on the roster for this market, with its province, owner and capacity."""
    path = roster_path(chemical, geography)
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = str(row.get("plant") or "").strip()
            if not name:
                continue
            kt = str(row.get("kt") or "").strip()
            try:
                capacity = float(kt) if kt else None
            except ValueError:
                capacity = None
            out.append({"name": name, "owner": str(row.get("owner") or "").strip() or None,
                        "province": str(row.get("province") or "").strip() or None,
                        "capacity_kt": capacity, "chemical": chemical, "geography": geography,
                        "from_source": path.name})
    return out


def region_named(text: str) -> str | None:
    """The sub-region a publisher's wording points at, if any.

    "西北某TDI工厂" and "a certain Northwest TDI plant" both say Northwest. Where no region is named
    the whole roster is the candidate set, which is still better than nothing.
    """
    lowered = str(text or "").lower()
    for word, _provinces in _CHINA_REGIONS.items():
        if word in lowered:
            return word
    # The Chinese terms, which is how the reports that prompted this are actually written.
    for chinese, word in (("西北", "northwest"), ("东北", "northeast"), ("华北", "north"),
                          ("华东", "east"), ("华南", "south"), ("华中", "central"),
                          ("西南", "southwest")):
        if chinese in text:
            return word
    return None


def candidates(chemical: str, geography: str, *, observed: str = "") -> list[dict]:
    """The suspects for one observation: the roster, narrowed by any region its wording names.

    Narrowing happens only where the wording names a region AND the roster records provinces for
    the candidates it would keep. A roster row with no province cannot be ruled in or out by region,
    so it stays a candidate - ruling it out would be guessing, and a blank province is our gap, not
    the plant's.
    """
    everything = plants(chemical, geography)
    region = region_named(observed)
    if not region:
        return everything
    provinces = _CHINA_REGIONS.get(region, ())
    kept = [p for p in everything
            if not p["province"] or p["province"] in provinces]
    return kept or everything


def by_capacity(found: list[dict]) -> list[dict]:
    """Largest first, because W4 asks which candidate's size fits the move."""
    return sorted(found, key=lambda p: (-(p["capacity_kt"] or 0.0), p["name"]))
