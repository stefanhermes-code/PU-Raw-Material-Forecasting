"""What HTC paid, drawn beside what the market published.

The same molecule from the same country, measured two ways: a published assessment of the Chinese
domestic spot market, and signed orders for Chinese material sold into Asia. Through 2024 they part
company - indexed to January, the assessment runs to 104 by March where the orders sit at 98, and by
October the assessment is at 78 while the orders and the European market are near 91. Neither is
wrong. They are different markets, and a buyer who only ever sees one of them is reading half a page.

Drawn as points, never as a continuous line: there are six orders across ten months, and joining them
would draw a price for weeks in which nothing was bought.

Free of Streamlit on purpose, the same way `forecast_cards` is, so what a chart says can be rendered
and checked without starting the application.
"""
from __future__ import annotations

from raw_material_forecasting import units
import pandas as pd
import plotly.graph_objects as go

TRANSACTION_COLOURS = ("#b45309", "#7c3aed", "#0f766e", "#be123c")


# How far before the published price a purchase series may reach and still belong on the same chart.
#
# The published China series starts in January 2024 and the KGI purchases start in July 2021, so a
# chart drawn to the published line alone throws away two and a half years of evidence about the same
# market. Widening it to cover the purchases is the point of this. What must NOT come with it is the
# FoamPartner history, which ends in February 2017: stretching the axis to 2014 to show it would
# squash a decade of nothing into the left half and make the years a buyer cares about unreadable.
#
# So a purchase series earns its place if it ran into, or close to, the period the published price
# covers. Two years is the judgement: KGI stops seven months before the published series starts and
# belongs; FoamPartner stops seven YEARS before it and does not.
RELEVANCE_MONTHS = 24


def _months_between(earlier: str, later: str) -> int:
    ey, em = int(earlier[:4]), int(earlier[5:7])
    ly, lm = int(later[:4]), int(later[5:7])
    return (ly - ey) * 12 + (lm - em)


def span_for(series: list[dict], published: tuple[str, str]) -> tuple[str, str]:
    """The date range the chart should cover: the published price, widened to the purchases that
    belong beside it.

    Returned rather than applied, so the caller sets the axis and the caller can be looked at.
    """
    first, last = published
    for entry in series or ():
        points = entry.get("points") or []
        if not points:
            continue
        dates = sorted(str(point.get("date", ""))[:10] for point in points if point.get("date"))
        if not dates:
            continue
        if _months_between(dates[-1], first) > RELEVANCE_MONTHS:
            continue                      # ended long before the published price begins
        first = min(first, dates[0])
        last = max(last, dates[-1])
    return first, last


def transaction_traces(series: list[dict], currency: str, target: str, table, stamp,
                        window: tuple | None = None):
    """One trace per purchase series, points only, labelled by what it is.

    A series with nothing inside the chart's own date range is dropped rather than drawn, because an
    empty trace still takes a line in the legend and a reader counts legend entries.
    """
    traces = []
    for index, entry in enumerate(series or ()):
        colour = TRANSACTION_COLOURS[index % len(TRANSACTION_COLOURS)]
        native = str(entry.get("currency") or "")
        points = entry.get("points") or []
        if window:
            first, last = window
            points = [p for p in points if first <= str(p.get("date", ""))[:10] <= last]
        if not points:
            continue

        def convert(value: float) -> float:
            if not native or native == target:
                return float(value)
            converted = table.convert(float(value), native, target, stamp)
            return converted[0] if converted else float(value)

        # Supplier and Incoterm, and nothing else. The chemical is the chart's own subject and the
        # origin is the filter that put this series on it, so repeating either in the legend says the
        # same thing four times; and an unknown origin has no business being announced as "UNKNOWN".
        basis = str(entry.get("basis") or "").strip()
        label = str(entry.get("label") or "")
        for prefix in (f"{entry.get('chemical', '')} ", ):
            if prefix.strip() and label.startswith(prefix):
                label = label[len(prefix):]
        supplier = label.split(" / ", 1)[-1] if " / " in label else label
        name = f"Paid - {supplier}" + (f", {basis}" if basis and basis != "DELIVERED" else "")
        signed = [p for p in points if p.get("evidence") == "purchase_order"]
        posted = [p for p in points if p.get("evidence") != "purchase_order"]
        for group, symbol, suffix in ((signed, "diamond", "order"),
                                      (posted, "circle-open", "ERP")):
            if not group:
                continue
            traces.append(go.Scatter(
                x=[pd.to_datetime(p["date"]) for p in group],
                y=[convert(p["price"]) for p in group],
                mode="markers", name=f"{name} ({suffix})",
                marker=dict(size=10, symbol=symbol, color=colour,
                            line=dict(width=1.6, color=colour)),
                customdata=[p.get("reference") or suffix for p in group],
                hovertemplate="%{x|%b %Y}<br>%{y:,.0f} " + target
                              + "/" + units.MT + "<br>%{customdata}<extra></extra>",
            ))
    return traces


