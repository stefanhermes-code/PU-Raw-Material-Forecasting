"""How the customs trade sources are counted on Data & Sources.

Eurostat and UN Comtrade publish a month weeks after it ends: Eurostat's July 2026 appeared on
15 September 2026. Judged on the observation month, every one of their series looks late every day of
the year. They are judged on when the reading became available instead, which the engine stores as
the publication date.

UN Comtrade is collected in several sources - a calendar year of months each, blocks of years for the
annual figures - so one market is not a dozen rows. They are counted as one feed per frequency, and a
predecessor customs code is counted with the code it continues.
"""
from __future__ import annotations

import re
from typing import Any

_LAGGED = ("comext_", "comtrade_")
_PREDECESSOR = re.compile(r"^(comext_.+_eu)_cn\d{8}$")


def is_lagged(source_id: str) -> bool:
    return str(source_id or "").startswith(_LAGGED)


def feed_of(source_id: str) -> str:
    """The feed a source's readings are counted under."""
    text = str(source_id or "")
    if text.startswith("comtrade_monthly_"):
        return "comtrade_monthly"
    if text.startswith("comtrade_annual_") and text != "comtrade_annual_bilateral":
        return "comtrade_annual"
    match = _PREDECESSOR.match(text)
    return match.group(1) if match else text


def arrival_day(row: dict[str, Any]) -> str:
    """The day a reading could first be seen: its publication date for the customs sources, its own
    date for everything else."""
    if is_lagged(str(row.get("source_id") or "")):
        published = str(row.get("publication_date") or "")[:10]
        if published:
            return published
    return str(row.get("observation_date") or "")[:10]


def arrival_days(source: dict[str, Any]) -> list[str]:
    """The days a source's recent readings became available, oldest last, off a `/sources` row.

    `arrival_day` makes this choice for one reading; this makes it for the list of days the engine
    sends with each source, and from the same `is_lagged`. Written here rather than where it is
    read so that the customs lag is decided once: judged on the observation month instead, every
    Eurostat and Comtrade series looks months behind on every day of the year.
    """
    if is_lagged(str(source.get("source_id") or "")):
        published = [str(day)[:10] for day in (source.get("recent_publication_days") or ()) if day]
        if published:
            return published
    return [str(day)[:10] for day in (source.get("recent_observation_days") or ()) if day]
