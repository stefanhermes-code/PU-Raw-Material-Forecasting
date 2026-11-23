"""What the application actually holds, market by market.

Stefan, 10 October 2026: "What I still miss is a simple overview of what is actually available in
the application like a matrix with on the Y-axis Chemical, Geography and on the X-axis Current
Market, Market Intelligence, Forecast."

One rule governs this file: each column asks the question its own page asks, with that page's own
test, so the matrix can never say a market is there and the page then say it is not.

  Current Market       we took a reading ourselves inside the collection window
  Market Intelligence  a capacity roster or developments of its own exist for the market
  Forecast             the market is still being priced in the forecast years

A chemical and geography with none of the three is not in the application, so it is not a row.
Nothing here counts anything or grades anything: it says present or absent, which is the whole
question being asked.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence

from raw_material_forecasting.market_lines import (CURRENT_WITHIN_DAYS, priced_recently,
                                                   priced_since)

CURRENT = "Current Market"
INTELLIGENCE = "Market Intelligence"
FORECAST = "Forecast"
COLUMNS = (CURRENT, INTELLIGENCE, FORECAST)


def _pairs(selections: Iterable[tuple]) -> set[tuple[str, str]]:
    """A chemical and a geography, with the Incoterm and the packaging dropped off."""
    return {(str(one[0]), str(one[1])) for one in selections if len(one) >= 2}


def matrix(identities: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]], *,
           sources: Sequence[dict[str, Any]] | None = None,
           chemicals: Sequence[str] = (), geographies: Sequence[str] = (),
           has_intelligence=None, forecast_from_year: int = 2026,
           as_of: date | None = None) -> list[dict[str, Any]]:
    """One row per market the application holds something for.

    `has_intelligence(chemical, geography)` is passed in rather than imported so this can be
    answered without the data directory being present.

    `sources` is accepted and no longer read. It said which sources we fetch, which is what the
    Current column asked until the page stopped asking it (3 November 2026). The parameter stays
    so every caller keeps working and so a later column that does need it has somewhere to look.
    """
    # The same rule the Current Market page is built from, so this report and that page can never
    # disagree about which markets are current. It asked `collected_recently` until 3 November
    # 2026, when the page stopped asking who took the reading; `sources` is no longer needed here
    # and the column is answered whether or not the caller can say which sources we fetch.
    current = _pairs(priced_recently(identities, observations, as_of=as_of,
                                     within_days=CURRENT_WITHIN_DAYS))
    # `as_of` reaches this one too. It used to be passed only to the Current Market test, which did
    # not matter while the Forecast test asked about the calendar year alone; it does now that the
    # test also asks how old the newest reading is, or the answer would be measured against the real
    # today rather than the date the caller is asking about.
    ahead = _pairs(priced_since(identities, observations,
                                from_year=forecast_from_year, as_of=as_of))
    told: set[tuple[str, str]] = set()
    if has_intelligence is not None:
        for chemical in chemicals:
            for geography in geographies:
                if has_intelligence(chemical, geography):
                    told.add((str(chemical), str(geography)))
    out = []
    for pair in sorted(current | ahead | told):
        out.append({
            "chemical": pair[0],
            "geography": pair[1],
            CURRENT: pair in current,
            INTELLIGENCE: pair in told,
            FORECAST: pair in ahead,
        })
    return out


def rows_for_display(rows: Sequence[dict[str, Any]], *, chemical_name=str,
                     geography_name=str, yes: str = "Yes", no: str = "") -> list[dict[str, str]]:
    """The same matrix with the names a reader knows and a mark in each cell."""
    out = []
    for row in rows:
        said = {"Chemical": chemical_name(row["chemical"]),
                "Geography": geography_name(row["geography"])}
        for column in COLUMNS:
            said[column] = yes if row.get(column) else no
        out.append(said)
    return sorted(out, key=lambda one: (one["Chemical"].lower(), one["Geography"].lower()))
