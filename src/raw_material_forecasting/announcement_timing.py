"""Does a producer announce a price change before the market price moves?

Correlation answers a question about cost. It cannot answer the question a buyer actually asks,
which is whether the producer is pricing on invoices or on the news. A producer who watches Brent and
raises the price on the expectation of dearer toluene moves *before* the cost arrives, and to a
monthly correlation that looks identical to fast cost transmission - both land in the same month.

An announcement is that behaviour made visible. It is dated, it is attributable, and it needs no
sample size: either the announcement landed before the market price moved, or it did not. This
measures that directly, for each announcement the store holds, and it says plainly when there are too
few to draw on rather than producing an average of three.

Nothing here is a forecast. It is a description of what has already happened, with the dates shown so
that any row can be checked against its source article.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

import pandas as pd

from .chemicals import canonical_code
from .fx import as_date as _as_date
from .ui_helpers import UNKNOWN as _UNKNOWN

PRODUCER_PRICE = "PRODUCER_PRICE"

# A move smaller than this is the market's own noise rather than a response to anything.
MATERIAL_MOVE = 0.01
# How long after an announcement it is still reasonable to call a move a response to it.
RESPONSE_WINDOW_DAYS = 30
# How far back to look for a move that was already under way when the announcement landed.
PRIOR_WINDOW_DAYS = 14
# Below this many matched announcements, no summary figure is offered at all.
MIN_ANNOUNCEMENTS = 5


@dataclass(frozen=True)
class Announcement:
    material: str
    announced_on: date
    publisher: str
    headline: str
    url: str
    # What the market price did afterwards
    price_before: float | None = None
    price_after: float | None = None
    moved_on: date | None = None
    days_to_move: int | None = None
    direction: str = ""
    already_moving: bool = False
    note: str = ""

    @property
    def matched(self) -> bool:
        return self.moved_on is not None


def producer_price_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Announcements only. A plant outage and a force majeure are events about supply, not about
    what a producer decided to charge, and mixing them would answer a different question."""
    out = []
    for event in events or ():
        if str(event.get("event_type") or "").upper() != PRODUCER_PRICE:
            continue
        out.append(event)
    return out


def _material_of(event: dict[str, Any]) -> str:
    """The event's material, as a current code.

    Through the code retirement the event lane kept writing `POLYOL` while the price lane had moved
    to `POLYETHER_POLYOL`, and an exact string match here reported "no announcements held" over four
    announcements that were sitting in the store. Codes are compared to codes everywhere else for
    this reason; this was the one lane that was missed.
    """
    data = event.get("data") or {}
    for key in ("product", "material"):
        value = data.get(key) or event.get(key)
        if value:
            return canonical_code(str(value).strip().upper().replace(" ", "_"))
    return ""


def _announced_on(event: dict[str, Any]) -> date | None:
    data = event.get("data") or {}
    for key in ("observation_date", "event_date", "publication_date"):
        day = _as_date(data.get(key))
        if day:
            return day
    return _as_date(event.get("available_from"))


def daily_prices(observations: Iterable[dict[str, Any]], material: str) -> list[tuple[date, float]]:
    """One price per day for a material, averaged across publishers.

    Averaged because two publishers quoting the same market on the same day is one price with two
    witnesses, and letting each one produce its own step would turn a change of publisher into a
    price move.
    """
    buckets: dict[date, list[float]] = {}
    wanted = canonical_code(material)
    for row in observations or ():
        if canonical_code(row.get("product_family")) != wanted:
            continue
        day = _as_date(row.get("observation_date"))
        price = row.get("price")
        if day is None or price is None:
            continue
        buckets.setdefault(day, []).append(float(price))
    return [(day, sum(values) / len(values)) for day, values in sorted(buckets.items())]


def _first_move(prices: Sequence[tuple[date, float]], start: date, end: date, threshold: float, *,
                base_before: bool = True) -> tuple[date, float, float] | None:
    """The first day in the window whose price differs from the baseline by at least `threshold`.

    For the window *after* an announcement the baseline is the last price before it - that is what
    "the price then moved" means. For the window *before* one, there may be no earlier price at all
    (a series that starts inside the window), and refusing to answer there would quietly mark every
    such announcement as landing into a quiet market. So the baseline falls back to the first price
    inside the window, which is the honest comparison available.
    """
    before = [(day, value) for day, value in prices if day <= start]
    inside = [(day, value) for day, value in prices if start < day <= end]
    if before:
        _base_day, base = before[-1]
        candidates = inside
    elif base_before or not inside:
        return None
    else:
        _base_day, base = inside[0]
        candidates = inside[1:]
    for day, value in candidates:
        if base and abs(value - base) / abs(base) >= threshold:
            return day, base, value
    return None


def announcement_timing(events: Iterable[dict[str, Any]],
                        observations: Iterable[dict[str, Any]], *,
                        material: str | None = None,
                        materials: Iterable[str] | None = None,
                        threshold: float = MATERIAL_MOVE,
                        response_days: int = RESPONSE_WINDOW_DAYS,
                        prior_days: int = PRIOR_WINDOW_DAYS) -> list[Announcement]:
    """One row per producer price announcement, with what the market price did around it."""
    # One chemical, or a whole chain. A producer raising propylene oxide is news for every polyether
    # polyol made from it, so the page asks for the raw material together with its feedstocks.
    wanted: set[str] | None = None
    if materials is not None:
        wanted = {canonical_code(code) for code in materials if str(code or "").strip()}
    elif material:
        wanted = {canonical_code(material)}
    prices_by_material: dict[str, list[tuple[date, float]]] = {}
    rows: list[Announcement] = []

    for event in producer_price_events(events):
        code = _material_of(event)
        if not code or (wanted is not None and code not in wanted):
            continue
        day = _announced_on(event)
        if day is None:
            continue
        data = event.get("data") or {}
        if code not in prices_by_material:
            prices_by_material[code] = daily_prices(observations, code)
        prices = prices_by_material[code]

        base = Announcement(
            material=code, announced_on=day,
            publisher=str(data.get("publisher") or ""),
            headline=str(data.get("headline") or ""),
            url=str(data.get("url") or ""),
        )
        if not prices:
            rows.append(_replace(base, note="No readings are held for this material."))
            continue

        after = _first_move(prices, day, day + timedelta(days=response_days), threshold)
        prior = _first_move(prices, day - timedelta(days=prior_days), day, threshold,
                            base_before=False)
        if after is None:
            rows.append(_replace(
                base, already_moving=prior is not None,
                note=f"No move of {threshold:.0%} or more within {response_days} days."))
            continue
        moved_on, before_price, after_price = after
        rows.append(_replace(
            base, price_before=before_price, price_after=after_price, moved_on=moved_on,
            days_to_move=(moved_on - day).days,
            direction="up" if after_price > before_price else "down",
            already_moving=prior is not None,
        ))
    rows.sort(key=lambda row: (row.material, row.announced_on))
    return rows


def _replace(row: Announcement, **changes) -> Announcement:
    from dataclasses import replace
    return replace(row, **changes)


def summarise(rows: Sequence[Announcement]) -> dict[str, Any]:
    """What the rows support, and nothing beyond it.

    Below a handful of matched announcements no figure is offered. An average of three is not a
    finding, and putting one on a screen invites it to be quoted as though it were.
    """
    matched = [row for row in rows if row.matched]
    summary: dict[str, Any] = {
        "announcements": len(rows),
        "matched_to_a_price_move": len(matched),
        "enough_to_summarise": len(matched) >= MIN_ANNOUNCEMENTS,
    }
    if not matched:
        summary["reading"] = (
            "No announcement in the store could be matched to a market price move, so nothing can "
            "be said yet about whether producers price ahead of the market."
        )
        return summary
    lead = sorted(row.days_to_move for row in matched)
    summary["median_days_to_move"] = lead[len(lead) // 2]
    summary["already_moving_when_announced"] = sum(1 for row in matched if row.already_moving)
    if not summary["enough_to_summarise"]:
        summary["reading"] = (
            f"Only {len(matched)} announcement(s) could be matched to a price move. The rows are "
            f"shown because each is checkable against its source, but {MIN_ANNOUNCEMENTS} or more "
            "are needed before the pattern means anything."
        )
    else:
        ahead = sum(1 for row in matched if not row.already_moving)
        summary["reading"] = (
            f"{ahead} of {len(matched)} announcements landed before the market price had started to "
            f"move, with a median of {summary['median_days_to_move']} days from announcement to "
            "move. An announcement that arrives after the market has already moved is the producer "
            "following the market rather than leading it."
        )
    return summary


COLUMNS = ("Material", "Announced", "Publisher", "Price moved", "Days after", "Direction",
           "Market already moving", "Note")

#: What an unknown looks like in any column of this table. One mark, so a reader learns it once,
#: and defined in `ui_helpers` so this table and the forecast record cannot drift apart.
UNKNOWN = _UNKNOWN


def timing_rows(rows: Sequence[Announcement]) -> list[dict[str, Any]]:
    """One row per announcement, every column text, an unknown shown as a dash.

    "Days after" used to carry `row.days_to_move` where the gap to the price move is known and the
    dash where it is not. A column holding an int and a str is an object column, Streamlit converts
    every dataframe it draws through Arrow, and Arrow refuses one outright:

        ArrowTypeError: ("Expected bytes, got a 'int' object",
                         'Conversion failed for column Days after with type object')

    That is not a blank cell on a table. It is an uncaught exception inside `st.dataframe`, so the
    whole application stopped the moment a material held both a matched announcement and an
    unmatched one. Measured against the live store on 31 October 2026: of the fourteen materials
    carrying announcements, four were in that state - Adipic Acid, Polyether Polyol, Polyol and
    Pure MDI. A material whose announcements were *all* unmatched was safe, because then the column
    was all dashes, which is why the crash looked random.

    **Text rather than a number, and that was measured too.** The obvious repair is to keep the
    count a number and let the empty cell be empty. Streamlit 1.63.0 with pandas 3.0.5 does not do
    that: a nullable `Int64` holding `pd.NA` prints the word **None** in the cell, a `float64`
    holding `NaN` prints **None**, and `column_config=NumberColumn(format="%d")` does not change
    it. Only a text column prints the dash. So this follows what `correlation.py:863` and
    `correlation_view.py:391` had already settled, both of them carrying the comment *"text: shares
    a column with the dash"* - which is what that comment has been trying to say.

    Nought stays nought. A producer announcing on the day the market moved is a finding, and `"0"`
    is not the dash.
    """
    return [
        {
            "Material": row.material.replace("_", " ").title() if row.material.isupper() and len(row.material) > 4 else row.material,
            "Announced": row.announced_on.isoformat(),
            "Publisher": row.publisher or "-",
            "Price moved": row.moved_on.isoformat() if row.moved_on else UNKNOWN,
            "Days after": str(row.days_to_move) if row.days_to_move is not None else UNKNOWN,
            "Direction": row.direction or UNKNOWN,
            "Market already moving": "yes" if row.already_moving else "no",
            "Note": row.note,
        }
        for row in rows
    ]


def timing_frame(rows: Sequence[Announcement]) -> pd.DataFrame:
    """The timing rows as a table the screen can draw.

    Here rather than at the two call sites in `correlation_view`, because both of them drew
    `pd.DataFrame(timing_rows(rows))` and either would have had to remember on its own. Naming the
    columns keeps an empty table a table rather than a frame with no columns at all.
    """
    return pd.DataFrame(timing_rows(rows), columns=list(COLUMNS)).astype("string")
