"""The Current Market and Forecast pages as branded Word reports.

Stefan, 23 September 2026: a report for each of these two pages, the selectors deciding what is in
it, the graph showing the last six months and the numbers in a table under it.

Built from the very frames the pages download, so a report and the screen that produced it cannot
disagree. The template, the logos, the type and the footer are the Market Intelligence report's -
one house style, one place to change it.
"""
from __future__ import annotations

from raw_material_forecasting import units
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from raw_material_forecasting.mi_report import (
    ASSETS, BLUE, BODY, GREY, H1, HTC_LOGO, NAVY, PI3_LOGO, RULE, SMALL, TITLE, _NS, _content_width,
    _core, _CONTENT_TYPES, _figures_table, _footer, _h1, _header, _HDR_RELS, _image, _p, _R, _RELS,
    _run, _STYLES, _table,
)
from raw_material_forecasting.page_chart import price_chart_png

# Six months of history on the graph (Stefan, 23 September 2026).
WINDOW_DAYS = 183
# Three months in the table under it (Stefan, 23 November 2026: "the table is a bit too long,
# 3 months history is enough"). The graph is for the shape of the market and wants the longer
# run; the table is for the numbers a buyer quotes, and those are recent.
TABLE_MONTHS = 3
CHART_CM = (16.5, 8.5)


def _doc_rels(with_chart: bool) -> str:
    chart = (f'<Relationship Id="rIdChart" Type="{_R}/image" Target="media/chart.png"/>'
             if with_chart else "")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rIdStyles" Type="{_R}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rIdHeader" Type="{_R}/header" Target="header1.xml"/>'
            f'<Relationship Id="rIdFooter" Type="{_R}/footer" Target="footer1.xml"/>'
            f'<Relationship Id="rIdHtc" Type="{_R}/image" Target="media/htc_global.png"/>'
            f"{chart}</Relationships>")


def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _title_block(headline: str, subtitle: str, today: date) -> list[str]:
    return [
        _p(_image("rIdHtc", 2.6, 2.6, 1, "HTC Global"), after=200),
        _p(_run(headline, size=TITLE, bold=True, color=NAVY), after=40),
        _p(_run(subtitle, size=H1, color=BLUE), after=60),
        _p(_run(today.strftime("%d %B %Y").lstrip("0"), size=BODY, color=GREY), after=240,
           rule_bottom=RULE),
    ]


def _chart_block(history, *, currency: str, forecast=None, band=None) -> tuple[str, bytes]:
    png = price_chart_png(history, currency=currency, forecast=forecast, band=band)
    return _p(_image("rIdChart", CHART_CM[0], CHART_CM[1], 2, "Price history"), after=80), png


def _window(rows: list[tuple[date, float]], days: int = WINDOW_DAYS) -> list[tuple[date, float]]:
    if not rows:
        return []
    cutoff = max(day for day, _value in rows) - timedelta(days=days)
    return [(day, value) for day, value in rows if day >= cutoff]


def _forecast_names(days: list[date]) -> tuple[str, list[str], list[str]]:
    """What each forecast row is called: its month, unless two of them share one.

    A monthly forecast is one figure per month and reads best under the month's name. A weekly one
    is three dates four weeks apart, and two of those can fall in the same month - 2 January and
    30 January are both "January 2027". Named by month, the table then carries the same name twice
    over two different numbers, which is what Stefan read as "the numbers are sometimes double" on
    23 November 2026. The cause that day was upstream and is fixed there; this is the report making
    sure it can never say one name twice again, whatever it is handed.

    Returns the column heading, the names for the table and the short ones for the headline band.
    """
    months = [day.strftime("%B %Y") for day in days]
    if len(set(months)) == len(months):
        return "Month", months, [day.strftime("%b %Y") for day in days]
    dated = [day.strftime("%d %b %Y").lstrip("0") for day in days]
    return "Date", dated, dated


def _last_months(rows: list[tuple[date, float]], months: int = TABLE_MONTHS):
    """The last few months as one row each: (first of month, price, low, high, prints, newest day).

    Stefan, 23 November 2026: "the table is a bit too long, 3 months history is enough". The table
    listed every reading in the window. On TDI Europe that was eleven rows, five of them the same
    2,700 on five consecutive days. On TDI China, which prints daily, it was eighty-nine rows - two
    pages of a report whose forecast is three figures.

    Cutting the window to three months does not fix a daily market: it still prints sixty-seven
    times in three months. So the table is by month, like the forecast above it, and on the same
    footing: a month's price is the **median** of its prints, which is exactly the figure the
    forecast is built from (`app.forecasting.service.bucket`). History and forecast can then be
    read against each other without converting one into the other in your head. Low and high are
    the month's lowest and highest print, so nothing the daily rows said is hidden - only folded.

    A month inside the run with no print is returned with a price of None rather than left out: a
    market that skipped October should say so, not show September next to November as if they met.
    """
    from statistics import median

    if not rows:
        return []
    newest = max(day for day, _value in rows)
    wanted = []
    index = newest.year * 12 + newest.month - 1
    for back in range(months - 1, -1, -1):
        wanted.append(date((index - back) // 12, (index - back) % 12 + 1, 1))
    out = []
    for start in wanted:
        inside = [(day, value) for day, value in rows
                  if (day.year, day.month) == (start.year, start.month)]
        if not inside:
            out.append((start, None, None, None, 0, None))
            continue
        values = [value for _day, value in inside]
        out.append((start, float(median(values)), min(values), max(values), len(values),
                    max(day for day, _value in inside)))
    return out


def _months_table(rows: list[tuple[date, float]], currency: str, width: int,
                  today: date) -> list[str]:
    """The last three months as a table, and the sentence that says what a row is."""
    months = _last_months(rows)
    if not any(price is not None for _s, price, _lo, _hi, _n, _last in months):
        return [_p(_run("This market has not printed a price in the last three months."))]
    body = []
    for start, price, low, high, _prints, last in months:
        name = start.strftime("%B %Y")
        # The month still running is marked with how far into it the prices go, so a November
        # figure on a report dated the 23rd is not read as the whole of November.
        if last is not None and (start.year, start.month) == (today.year, today.month):
            name += f" (to {last.strftime('%d %b').lstrip('0')})"
        if price is None:
            body.append([name, "no price printed", "-", "-"])
        else:
            body.append([name, f"{price:,.0f}", f"{low:,.0f}", f"{high:,.0f}"])
    return [
        _table(["Month", f"Price {units.pair(currency)}", "Low", "High"], body,
               [int(width * .34), int(width * .22), int(width * .22), int(width * .22)],
               numeric={1, 2, 3}),
        _p(_run("A month's price is the middle of the prices printed in it. The forecast is built "
                "from these, and for the month still running from its latest price. Low and high "
                "are the month's lowest and highest print.",
                size=SMALL, italic=True, color=GREY), after=160),
    ]


def _package(body: str, title: str, chart: bytes | None, assets: Path) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_NS}><w:body>{body}'
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
        '<w:footerReference w:type="default" r:id="rIdFooter"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1418" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" '
        'w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("docProps/core.xml", _core(title))
        archive.writestr("word/_rels/document.xml.rels", _doc_rels(chart is not None))
        archive.writestr("word/_rels/header1.xml.rels", _HDR_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/header1.xml", _header())
        archive.writestr("word/footer1.xml", _footer(title))
        archive.writestr("word/document.xml", document)
        archive.writestr("word/media/pi3_logo.png", (assets / PI3_LOGO.name).read_bytes())
        archive.writestr("word/media/htc_global.png", (assets / HTC_LOGO.name).read_bytes())
        if chart is not None:
            archive.writestr("word/media/chart.png", chart)
    return buffer.getvalue()


def _market_line(market: dict) -> str:
    return " · ".join(str(value) for value in market.values() if str(value or "").strip())


def file_name(kind: str, market: dict, today: date | None = None) -> str:
    today = today or date.today()
    where = "_".join(str(value).replace(" ", "-") for value in market.values()
                     if str(value or "").strip())
    return f"{kind}_{where}_{today:%Y%m%d}.docx"


def current_market_docx(history, *, market: dict, currency: str, kicker: str, price: str,
                        priced: str, note: str = "", today: date | None = None,
                        assets: Path = ASSETS) -> bytes:
    """The Current Market page for one selection: the price, six months of it, and the numbers."""
    today = today or date.today()
    rows = _window([(day, value) for day, value in
                    ((_as_date(d), v) for d, v in history or []) if day is not None])
    width = _content_width()
    title = f"{_market_line(market)} Current Market"
    chart, png = _chart_block(rows, currency=currency)
    parts = _title_block(_market_line(market), "Current Market Report", today)
    parts.append(_figures_table({kicker: price, "Priced": priced, "Currency": units.per_unit(currency)},
                                width))
    if note:
        parts.append(_p(_run(note, size=SMALL, italic=True, color=GREY), after=160))
    parts.append(_h1("Price, last six months"))
    parts.append(chart)
    # The numbers start on their own page (Stefan, 23 September 2026): the graph has the first
    # page to itself. Tried without the break on 23 November once the table was three rows, and
    # rendered it: the table fitted under the graph and its caption fell alone onto page two.
    parts.append(_h1("The last three months", page_break=True))
    parts.extend(_months_table(rows, currency, width, today))
    return _package("".join(parts), title, png, assets)


def forecast_docx(history, forecast, *, market: dict, currency: str, today_price: str,
                  priced: str, note: str = "", today: date | None = None,
                  assets: Path = ASSETS) -> bytes:
    """The Forecast page for one selection.

    `forecast` is (period, value, low, high) per horizon, exactly the rows the page's chart draws.
    """
    today = today or date.today()
    rows = _window([(day, value) for day, value in
                    ((_as_date(d), v) for d, v in history or []) if day is not None])
    ahead = [(day, float(value), None if low is None else float(low),
              None if high is None else float(high))
             for day, value, low, high in
             ((_as_date(p), v, lo, hi) for p, v, lo, hi in forecast or []) if day is not None]
    width = _content_width()
    title = f"{_market_line(market)} Forecast"
    band = [(day, low, high) for day, _v, low, high in ahead if low is not None and high is not None]
    chart, png = _chart_block(rows, currency=currency,
                              forecast=[(day, value) for day, value, _lo, _hi in ahead], band=band)
    # Every month of the forecast in the headline band, not just the first.
    #
    # Stefan, 23 November 2026, on a TDI Europe report: *"it shows only one month"*, and then
    # *"The forecast should be 3 months, not the rendering of the whole graph"*. The band showed
    # `ahead[0]` alone - November - while the table under it carried November, December and
    # January. A reader looking at the top of the report saw a one-month forecast.
    #
    # The unit is stated once, on Today, and the months carry the number. Five short columns read
    # better than five long ones, and the unit cannot be in doubt three columns later.
    heading, long_names, short_names = _forecast_names([day for day, _v, _lo, _hi in ahead])
    figures = {"Today": today_price, "Priced": priced}
    for name, (_day, value, _low, _high) in zip(short_names, ahead):
        figures[name] = f"{value:,.0f}"
    parts = _title_block(_market_line(market), "Forecast Report", today)
    parts.append(_figures_table(figures, width))
    if note:
        parts.append(_p(_run(note, size=SMALL, italic=True, color=GREY), after=160))
    parts.append(_h1("Price and forecast, last six months"))
    parts.append(chart)
    # The graph has the first page to itself (Stefan, 23 September 2026) and the numbers have the
    # second: the months behind first, then the months ahead. The two tables have the same four
    # columns at the same widths, so they read straight down as one run of time - where the
    # history alone used to run to two pages and push the forecast between them.
    parts.append(_h1("The last three months", page_break=True))
    parts.extend(_months_table(rows, currency, width, today))
    if ahead:
        parts.append(_h1("The forecast"))
        parts.append(_table([heading, f"Forecast {units.pair(currency)}", "Low", "High"],
                            [[name, f"{value:,.0f}",
                              "-" if low is None else f"{low:,.0f}",
                              "-" if high is None else f"{high:,.0f}"]
                             for name, (_day, value, low, high) in zip(long_names, ahead)],
                            [int(width * .34), int(width * .22), int(width * .22), int(width * .22)],
                            numeric={1, 2, 3}))
        parts.append(_p(_run("The low and high are the range the forecast has held eight times in "
                             "ten when it was measured against what the market did.",
                             size=SMALL, italic=True, color=GREY), after=160))
    return _package("".join(parts), title, png, assets)
