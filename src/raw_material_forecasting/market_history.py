"""Long price history drawn beside a market's own price, on Current Market and Forecast.

Stefan, 22 September 2026: the history of TDI, pMDI and polyether polyol in Europe belongs on both
charts. It is a monthly Western Europe DDP series from 2014 to July 2026, held in the store as a
reference series. It is drawn as its own line next to the market price and never merged into it: a
delivered price from 2015 is not averaged with a 2026 regional price.

No source is named on screen; the line is named for what it is.
"""
from __future__ import annotations

from raw_material_forecasting.chemicals import canonical_code

# Store code -> the reference series' label. MDI is polymeric MDI: in flexible foam MDI means pMDI.
EUROPE_HISTORY = {
    "TDI": "TDI WE DDP",
    "PMDI": "MDI WE DDP",
    "POLYETHER_POLYOL": "Flexible slabstock polyol WE DDP",
}
# A published, non-negotiated price (Stefan, 22 September 2026): gross, before rebates, which is why
# it can sit well above what contract buyers pay. Said on the chart; no source named.
HISTORY_NAME = "Published price, Western Europe DDP"


def history_lines(reference_series, chemical: str, geography: str) -> list[dict]:
    """The history lines for one market, in the currency they are held in."""
    if str(geography or "").strip() != "Europe":
        return []
    label = EUROPE_HISTORY.get(canonical_code(chemical).replace(" ", "_"))
    if not label:
        return []
    for series in reference_series or []:
        if series.get("label") != label or series.get("is_index"):
            continue
        points = [(str(p.get("date"))[:10], float(p["value"]))
                  for p in series.get("observations") or [] if p.get("value") is not None]
        if len(points) < 2:
            continue
        currency = str(series.get("unit") or "").split("/", 1)[0].strip().upper() or None
        return [{"name": HISTORY_NAME, "currency": currency, "points": points}]
    return []


PURCHASE_NAME = "Europe delivered prices"


def purchase_lines(transactions, chemical: str, geography: str) -> list[dict]:
    """European delivered purchase prices, 2013-2017, as one monthly line per currency.

    Stefan, 22 September 2026: this history goes on the customer charts, and no source is named.
    Only Europe: the purchase records for Asia stay off customer pages. Several grades bought in the
    same month are averaged into that month, which is what one line of history can honestly show.
    """
    if str(geography or "").strip() != "Europe":
        return []
    code = canonical_code(chemical).replace(" ", "_")
    months: dict[tuple[str, str], list[float]] = {}
    for series in transactions or []:
        if canonical_code(series.get("chemical") or "").replace(" ", "_") != code:
            continue
        if str(series.get("destination") or "").strip() not in {"Europe", ""}:
            continue
        currency = str(series.get("currency") or "").upper() or None
        for point in series.get("points") or []:
            if point.get("price") is None or not point.get("date"):
                continue
            months.setdefault((currency, str(point["date"])[:7] + "-01"), []).append(float(point["price"]))
    lines = []
    for currency in sorted({c for c, _ in months}, key=str):
        points = sorted((day, sum(v) / len(v)) for (c, day), v in months.items() if c == currency)
        if len(points) >= 2:
            lines.append({"name": PURCHASE_NAME, "currency": currency, "points": points, "style": "points"})
    return lines
