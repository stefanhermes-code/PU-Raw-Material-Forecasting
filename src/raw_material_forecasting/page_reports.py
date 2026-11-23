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

# Six months of history on the graph and in the table (Stefan, 23 September 2026).
WINDOW_DAYS = 183
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
    # The numbers start on their own page (Stefan, 23 September 2026): the graph has the first page
    # to itself and the table is read on the second rather than half of it hanging off the first.
    parts.append(_h1("The figures behind the graph", page_break=True))
    if rows:
        parts.append(_table(["Date", f"Price {units.pair(currency)}"],
                            [[day.strftime("%d %b %Y").lstrip("0"), f"{value:,.0f}"]
                             for day, value in reversed(rows)],
                            [int(width * .5), int(width * .5)], numeric={1}))
    else:
        parts.append(_p(_run("This market has no price history in the last six months.")))
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
    figures = {"Today": today_price, "Priced": priced}
    if ahead:
        figures[ahead[0][0].strftime("%B %Y")] = units.with_unit(f"{currency} {ahead[0][1]:,.0f}")
    parts = _title_block(_market_line(market), "Forecast Report", today)
    parts.append(_figures_table(figures, width))
    if note:
        parts.append(_p(_run(note, size=SMALL, italic=True, color=GREY), after=160))
    parts.append(_h1("Price and forecast, last six months"))
    parts.append(chart)
    first_after_the_graph = True
    if ahead:
        parts.append(_h1("The forecast", page_break=True))
        first_after_the_graph = False
        parts.append(_table(["Month", f"Forecast {units.pair(currency)}", "Low", "High"],
                            [[day.strftime("%B %Y"), f"{value:,.0f}",
                              "-" if low is None else f"{low:,.0f}",
                              "-" if high is None else f"{high:,.0f}"]
                             for day, value, low, high in ahead],
                            [int(width * .28), int(width * .24), int(width * .24), int(width * .24)],
                            numeric={1, 2, 3}))
        parts.append(_p(_run("The low and high are the range the forecast has held eight times in "
                             "ten when it was measured against what the market did.",
                             size=SMALL, italic=True, color=GREY), after=160))
    parts.append(_h1("The price behind the graph", page_break=first_after_the_graph))
    if rows:
        parts.append(_table(["Date", f"Price {units.pair(currency)}"],
                            [[day.strftime("%d %b %Y").lstrip("0"), f"{value:,.0f}"]
                             for day, value in reversed(rows)],
                            [int(width * .5), int(width * .5)], numeric={1}))
    else:
        parts.append(_p(_run("This market has no price history in the last six months.")))
    return _package("".join(parts), title, png, assets)
