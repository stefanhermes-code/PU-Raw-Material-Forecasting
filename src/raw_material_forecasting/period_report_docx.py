"""The period report as a branded Word document.

Stefan, 8 October 2026, after talking to Darren Millar and hearing that Neveon want it too: a
monthly or quarterly report on raw-material price movements, produced by HTC Global under the PI3
brand, that a customer can hand to their own customers. It shows the graphs and tells the story of
the period.

The house style is the one the Market Intelligence report already uses - the same template, logos,
type and footer - so there is one place to change how an HTC document looks.

Three rules this document is written under, all Stefan's. No source is named anywhere: everything in
it is HTC Global's own market research and the first page says so. Every figure comes from
`period_report` and every sentence from `period_narrative`, so nothing in a customer's hands was
composed rather than computed. And a market with nothing new in the period is written up rather than
dropped, because a gap in a report for a customer reads as a mistake.
"""
from __future__ import annotations

from raw_material_forecasting import units
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Sequence

from raw_material_forecasting import period_narrative as prose
from raw_material_forecasting.chemicals import display_chemical
from raw_material_forecasting.mi_report import (
    ASSETS, BODY, GREY, HTC_LOGO, NAVY, PI3_LOGO, SMALL, TITLE, _NS, _core, _CONTENT_TYPES,
    _field, _footer, _h1, _h2, _header, _HDR_RELS, _image, _R, _RELS, _run, _STYLES, _table,
    BLUE, RULE,
)
from raw_material_forecasting.mi_report import _p as _paragraph


# Charlie, 10 October 2026: "On page 1, the TDI analysis paragraph breaks after 'on', continuing
# with 'three readings' on page 2. Keep paragraphs together where practical, and ensure that
# headings remain with their following content. Apply this throughout the Word document."
#
# Every paragraph in this report is written whole on one page unless it is taller than a page, in
# which case Word splits it anyway. The headings already carry keepNext, which is the second half
# of what he asked for, and the tiles and boxes already carry cantSplit.
def _p(runs: str, **how) -> str:
    how.setdefault("keep_lines", True)
    return _paragraph(runs, **how)
from raw_material_forecasting.page_chart import price_chart_png
from raw_material_forecasting.period_report import MarketPeriod, Period, headline, spreads

CHART_CM = (16.5, 8.5)
CONTACT_NAME = "Stefan Hermes"
CONTACT_EMAIL = "stefan.hermes@htcglobal.asia"
COPYRIGHT = ("\u00a9 HTC Global. PI3 is a product brand of HTC Global. All rights reserved.")



# A legal notice says "are not an offer" because that is what it has to say. The writing gates
# read market prose, so the standing notices are handed to them as exempt.
STANDING_NOTICES = (COPYRIGHT,)


# The furniture of the redesigned report, 9 October 2026.
#
# Stefan put Charlie's version of the same quarter beside ours: five pages against our seven, and
# more in it. What made the difference was not content, it was shape - a running band instead of a
# page break before every section, four figures a reader takes in at a glance, and the long prose
# of a method section turned into nothing at all.
#
# And what he would not take from it either: "no meta leaking, disclosing of methods, etc. Show
# confidence." So there is no evidence-status column here, no methodology table, and no sentence
# anywhere explaining how the application arrives at what it says. The report states what is.
TILE_FILL = "F2F6FA"
BOX_FILL = "E8EEF5"
PAGE_WIDTH = 9640


ORANGE = "C55A11"
PANEL_FILL = "FFFFFF"
PANEL_LINE = "D7E0EA"
TILE_FILL_V21 = "F2F7FB"
ARROW_UP, ARROW_DOWN, ARROW_FLAT = "\u2197", "\u2198", "\u2014"
# Markets, not chemicals: the tiles count price series, and one chemical can rise in one place and
# fall in another. Stefan's adipic acid report of 14 October 2026 showed "Chemicals analysed" over
# a count of adipic acid markets.
RISING, FALLING, UNCHANGED, ANALYSED = (
    "Markets rising", "Markets falling", "Markets unchanged", "Markets analysed")
PARTIAL_NOTE = "Price movements reflect available observations within the reporting period."


# The two heading levels, as Stefan set them in the revised template of 12 October 2026. Word's
# own Heading 1 and Heading 2, which means a reader can fold the document in the navigation pane
# and an editor can restyle every heading from one place without touching the generator.
HEADING_COLOUR = "0F4761"
HEADING_FONT = "Calibri Light"
_HEADING_STYLE = (
    '<w:style w:type="paragraph" w:styleId="Heading{level}">'
    '<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
    '<w:uiPriority w:val="9"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="{before}" w:after="0"/>'
    '<w:outlineLvl w:val="{outline}"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>'
    '<w:color w:val="{colour}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:style>')


def _monitor_styles() -> str:
    """The house styles with Word's heading levels added, for this report only.

    The Market Intelligence report shares `_STYLES` and is not touched by this.
    """
    added = (_HEADING_STYLE.format(level=1, before=240, outline=0, size=32,
                                   colour=HEADING_COLOUR, font=HEADING_FONT)
             + _HEADING_STYLE.format(level=2, before=40, outline=1, size=26,
                                     colour=HEADING_COLOUR, font=HEADING_FONT))
    return _STYLES.replace("</w:styles>", added + "</w:styles>")


def _heading(text: str, level: int = 1, *, page_break: bool = False) -> str:
    """A section opener, in the document's own heading style."""
    # keepNext and keepLines are in the style as well. They are written here too, so a heading
    # holds to what follows it even where the style is overridden or the document is opened by
    # something that resolves styles differently.
    props = ('<w:pPr><w:pStyle w:val="Heading%d"/>%s<w:keepNext/><w:keepLines/>'
             '<w:spacing w:before="%d" w:after="%d"/></w:pPr>'
             % (level, "<w:pageBreakBefore/>" if page_break else "",
                320 if level == 1 else 200, 80 if level == 1 else 40))
    return "<w:p>" + props + _run(text, size=32 if level == 1 else 26,
                                  color=HEADING_COLOUR) + "</w:p>"


def _named(text: str) -> str:
    """A heading that names a picture rather than opening a section: the supply chain."""
    return _p(_run(text, size=30, bold=True, color=NAVY), before=300, after=100,
              keep_next=True, keep_lines=True)


def _chain_title(chemical: str) -> str:
    """What the chain picture is called. Stefan's wording, 12 October 2026."""
    return f"{display_chemical(chemical)} upstream supply chain"


def _rule_run(width_twips: int = 320) -> str:
    """The short orange rule that opens a section, drawn as a bottom border on a narrow cell."""
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
            '<w:tcBorders><w:bottom w:val="single" w:sz="18" w:space="0" w:color="%s"/>'
            '</w:tcBorders><w:vAlign w:val="center"/></w:tcPr>'
            '<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr></w:p></w:tc>'
            % (width_twips, ORANGE))


def _section(title: str, *, page_break: bool = False) -> str:
    """A section opener.

    Stefan's revised template, 12 October 2026, replaced the ruled band with Word's own heading
    style: lighter, larger, and foldable in the navigation pane. The orange rule stays where it
    began, under the masthead on the reporting window.
    """
    return _heading(title, 1, page_break=page_break)


def _window_rule(text: str) -> str:
    """The reporting window under the masthead: the orange rule, then the dates, set quietly."""
    label = _p(_run(text, size=BODY, color="1F2D3D"), before=0, after=0)
    cell = ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>%s</w:tc>'
            % (PAGE_WIDTH - 540, label))
    row = _rule_run(320) + ('<w:tc><w:tcPr><w:tcW w:w="220" w:type="dxa"/></w:tcPr>'
                            '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p></w:tc>') + cell
    return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>'
            '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:left w:val="nil"/>'
            '<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
            '<w:tblGrid><w:gridCol w:w="320"/><w:gridCol w:w="220"/><w:gridCol w:w="%d"/></w:tblGrid>'
            '<w:tr><w:trPr><w:cantSplit/></w:trPr>%s</w:tr></w:tbl>'
            % (PAGE_WIDTH, PAGE_WIDTH - 540, row)) + _p("", after=60)


def _panel(title: str, picture: str, observed: str, width: int) -> str:
    """One chart in a panel, with the market and the currency over it.

    The observed window used to be printed under the chart as well. Stefan took it out of the
    template on 12 October 2026: the paragraph beside the chart already gives the days, and the
    caption repeated them a centimetre away.
    """
    body = (_p(_run(title, size=SMALL, bold=True, color=NAVY), after=60, keep_next=True)
            + _p(picture, after=0, align="center"))
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
            '<w:tcBorders>%s</w:tcBorders>'
            '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
            '<w:tcMar><w:top w:w="120" w:type="dxa"/><w:bottom w:w="120" w:type="dxa"/>'
            '<w:left w:w="140" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tcMar>'
            '</w:tcPr>%s</w:tc>'
            % (width, "".join('<w:%s w:val="single" w:sz="6" w:space="0" w:color="%s"/>'
                              % (side, PANEL_LINE)
                              for side in ("top", "bottom", "left", "right")),
               PANEL_FILL, body))


def _panel_row(panels: list[tuple[str, str, str]]) -> str:
    """Two or three chart panels across the page, as the reference layout sets them."""
    if not panels:
        return ""
    gap = 140
    width = (PAGE_WIDTH - gap * (len(panels) - 1)) // len(panels)
    cells, grid = "", ""
    for index, (title, picture, observed) in enumerate(panels):
        if index:
            cells += ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>'
                      '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p></w:tc>' % gap)
            grid += '<w:gridCol w:w="%d"/>' % gap
        cells += _panel(title, picture, observed, width)
        grid += '<w:gridCol w:w="%d"/>' % width
    return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>'
            '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:left w:val="nil"/>'
            '<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
            '<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>%s</w:tblGrid>'
            '<w:tr><w:trPr><w:cantSplit/></w:trPr>%s</w:tr></w:tbl>'
            % (PAGE_WIDTH, grid, cells)) + _p("", after=100)


def _summary_tiles(facts: dict, markets: Sequence = (), period=None,
                   currency: str = "USD") -> str:
    """The four figures at the top of the first page.

    An edition covering one market shows that market's own figures, because counting to one is
    not intelligence. An edition covering several counts them, in the specification's words.
    """
    own = _headline_tiles(list(markets), facts, currency, period) if markets and period else []
    if len(list(markets)) == 1 and own:
        return _tiles(own)
    return _counted_tiles(facts)


def _counted_tiles(facts: dict) -> str:
    """Rising, falling, unchanged and analysed, with the direction drawn beside the figure.

    The wording is the specification's. A market with no direction is counted in none of the three
    and not in `analysed` either, because a figure a reader adds up has to add up.
    """
    rose, fell = int(facts.get("rose", 0)), int(facts.get("fell", 0))
    held = int(facts.get("held", 0))
    analysed = rose + fell + held
    cells = [(str(rose), ARROW_UP, "1E7A3C", RISING),
             (str(fell), ARROW_DOWN, "B42318", FALLING),
             (str(held), ARROW_FLAT, GREY, UNCHANGED),
             (str(analysed), "", GREY, ANALYSED)]
    width = PAGE_WIDTH // len(cells)
    row = ""
    for value, arrow, colour, label in cells:
        figure = _run(value, size=44, bold=True, color=NAVY)
        if arrow:
            figure += _run("   " + arrow, size=36, bold=True, color=colour)
        body = (_p(figure, after=20)
                + _p(_run(label, size=SMALL, color="1F2D3D"), after=0))
        row += ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
                '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
                '<w:tcMar><w:top w:w="140" w:type="dxa"/><w:bottom w:w="140" w:type="dxa"/>'
                '<w:left w:w="180" w:type="dxa"/><w:right w:w="180" w:type="dxa"/></w:tcMar>'
                '</w:tcPr>%s</w:tc>' % (width, TILE_FILL_V21, body))
    grid = "".join('<w:gridCol w:w="%d"/>' % width for _one in cells)
    return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>'
            '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:left w:val="nil"/>'
            '<w:right w:val="nil"/><w:insideH w:val="nil"/>'
            '<w:insideV w:val="single" w:sz="6" w:space="0" w:color="%s"/></w:tblBorders>'
            '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
            '<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>%s</w:tblGrid>'
            '<w:tr><w:trPr><w:cantSplit/></w:trPr>%s</w:tr></w:tbl>'
            % (PAGE_WIDTH, PANEL_LINE, TILE_FILL_V21, grid, row))


def _route_cm(steps: int) -> float:
    """The picture's own height, so Word does not stretch a two-row chain into a one-row box."""
    steps = max(int(steps or 0), 1)
    per_row = min(steps, 8)
    rows = -(-steps // per_row)
    return round(16.5 * (70 + rows * 150 + 40) / 1650, 2)


def _band(text: str, *, page_break: bool = False) -> str:
    """The ruled band the report used before 12 October 2026.

    Kept because the Market Intelligence report and the checks that read this module still name
    it. Nothing in the monitor draws one: sections open with `_section`, which is Word's own
    heading style, as Stefan's revised template sets them.
    """
    return _p(_run(text.upper(), size=SMALL, bold=True, color=BLUE, caps=True),
              before=280, after=120, rule_bottom=BLUE, keep_next=True, page_break=page_break)


def _tiles(cells: list[tuple[str, str, str]]) -> str:
    """A row of figures: a small label, the number, and what it belongs to."""
    if not cells:
        return ""
    width = PAGE_WIDTH // len(cells)
    row = ""
    for label, value, under in cells:
        body = (_p(_run(label, size=16, bold=True, color=GREY), after=40)
                + _p(_run(value, size=30, bold=True, color=NAVY), after=30))
        if under:
            body += _p(_run(under, size=14, color=GREY), after=0)
        row += (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>'
                f'<w:shd w:val="clear" w:color="auto" w:fill="{TILE_FILL}"/>'
                '<w:tcMar><w:top w:w="120" w:type="dxa"/><w:bottom w:w="120" w:type="dxa"/>'
                '<w:left w:w="120" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>'
                f'</w:tcPr>{body}</w:tc>')
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for _cell in cells)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{PAGE_WIDTH}" w:type="dxa"/>'
            '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:left w:val="nil"/>'
            '<w:right w:val="nil"/><w:insideH w:val="nil"/>'
            '<w:insideV w:val="single" w:sz="12" w:space="0" w:color="FFFFFF"/></w:tblBorders>'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
            f"<w:tblGrid>{grid}</w:tblGrid>"
            f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{row}</w:tr></w:tbl>' + _p("", after=160))


def _caption(text: str) -> str:
    return _p(_run(text, size=SMALL, italic=True, color=GREY), after=160)


def _doc_rels(charts: int, photos: dict | None = None) -> str:
    pictures = "".join(
        f'<Relationship Id="rIdChart{index}" Type="{_R}/image" Target="media/chart{index}.png"/>'
        for index in range(charts))
    pictures += "".join(
        f'<Relationship Id="rIdPhoto{key}" Type="{_R}/image" Target="media/photo_{key}.png"/>'
        for key in (photos or {}))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rIdStyles" Type="{_R}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rIdHeader" Type="{_R}/header" Target="header1.xml"/>'
            f'<Relationship Id="rIdFooter" Type="{_R}/footer" Target="footer1.xml"/>'
            f'<Relationship Id="rIdPi3" Type="{_R}/image" Target="media/pi3_logo.png"/>'
            f'<Relationship Id="rIdHtc" Type="{_R}/image" Target="media/htc_global.png"/>'
            f"{pictures}</Relationships>")


def charts_order(charts: dict[str, bytes], key: str) -> int:
    return list(charts).index(key)


def _joined_places(names: Sequence[str]) -> str:
    seen = list(dict.fromkeys(str(name) for name in names if str(name).strip()))
    if len(seen) <= 1:
        return seen[0] if seen else ""
    return ", ".join(seen[:-1]) + f" and {seen[-1]}"


def _regions(markets: Sequence[MarketPeriod], period: Period,
             chemical: str = "") -> list[str]:
    """Where one material is priced in more than one of the chosen regions, how far apart."""
    gaps = [one for one in spreads(markets)
            if not chemical or str(one.get("chemical")) == chemical]
    if not gaps:
        return []
    parts = [_section("Where the regions differ")]
    for gap in gaps:
        parts.append(_p(_run(prose.spread_paragraph(gap, period))))
    return parts


def _short_window(row: MarketPeriod, period: Period) -> str:
    """A tile has room for a window, not for a sentence."""
    if row.band == "FULL":
        return period.name
    opened, closed = str(row.opened_on)[:10], str(row.closed_on)[:10]
    if not opened or not closed:
        return period.name
    from datetime import date as _date

    first, last = _date.fromisoformat(opened), _date.fromisoformat(closed)
    if first.year == last.year and first.month == last.month:
        return f"{first.day}-{last.day} {last:%b %Y}"
    return f"{first:%d %b}".lstrip("0") + " to " + f"{last:%d %b %Y}".lstrip("0")


def _all_full(markets: Sequence[MarketPeriod]) -> bool:
    return bool(markets) and all(row.band == "FULL" for row in markets)


def _headline_tiles(markets: Sequence[MarketPeriod], facts: dict, currency: str,
                    period: Period):
    """Four figures whose labels are the whole description, so nothing needs decoding."""
    moved = [row for row in markets if row.change_percent is not None]
    if len(markets) == 1 and moved:
        row = moved[0]
        before = period.previous.name
        window = _short_window(row, period)
        return [(f"Price on {prose.day_in_words(row.closed_on)}",
                 prose.money(row.closed, row.currency), ""),
                (f"Change over {window}", f"{row.change_percent:+.1f}%", ""),
                (f"Range over {window}",
                 units.with_unit(f"{currency} {row.low:,.0f} - {row.high:,.0f}"), ""),
                (f"Change over {before}",
                 f"{row.previous_change:+.1f}%" if row.previous_change is not None else "-",
                 "")]
    if not moved:
        return []
    biggest = facts.get("biggest")
    whole = _all_full(moved)
    over = f"over {period.name}" if whole else "in the observed window"
    return [("Markets in this edition", str(facts.get("markets", 0)), ""),
            (f"Higher {over}", str(facts.get("rose", 0)), ""),
            (f"Lower {over}", str(facts.get("fell", 0)), ""),
            ("Largest move",
             f"{biggest.change_percent:+.1f}%" if biggest is not None else "-",
             f"{display_chemical(biggest.chemical)} in {biggest.geography}, "
             f"{_short_window(biggest, period)}" if biggest is not None else "")]


def _pictures(start: int = 10):
    """A number per picture, handed out as the sections are built."""
    number = [start]

    def next_one() -> int:
        number[0] += 1
        return number[0]

    return next_one


def _written_for(narrative, section: str, chemical: str,
                 rows: Sequence[MarketPeriod]) -> str:
    """The engine's paragraph for one market, found by the name it was given."""
    held = dict(getattr(narrative, section, {}) or {})
    if not held:
        return ""
    by_name = {" ".join(str(key).split()).lower(): value for key, value in held.items()}
    for row in rows:
        for name in (f"{display_chemical(chemical)} in {row.geography}",
                     f"{chemical} {row.geography}", f"{chemical}|{row.geography}", chemical):
            found = by_name.get(" ".join(str(name).split()).lower())
            if found:
                return str(found)
    return str(next(iter(held.values()), "")) if len(held) == 1 else ""


def _route_note(narrative, chemical: str, rows: Sequence[MarketPeriod], *, alone: bool) -> str:
    """The engine's paragraph about how this material is made."""
    held = getattr(narrative, "route", "")
    if isinstance(held, dict):
        return _written_for(narrative, "route", chemical, rows)
    said = str(held or "").strip()
    if not said:
        return ""
    if alone:
        return said
    return said if display_chemical(chemical).lower() in said.lower() else ""


# What the chain diagram is for, said under it. Stefan, 11 October 2026.
ROUTE_CAPTIONS = {
    "TDI": ("TDI production chain, showing the principal chemical intermediates and the upstream "
            "inputs relevant to production costs and material availability."),
    "POLYETHER_POLYOL": ("Polyether polyol production chain, highlighting oxide feedstocks, "
                         "upstream chemical dependencies and their relevance to production "
                         "economics."),
}


def route_caption(chemical: str) -> str:
    """The approved caption for this material, or the same sentence written for it."""
    held = ROUTE_CAPTIONS.get(str(chemical).upper())
    if held:
        return held
    return (f"{display_chemical(chemical)} production chain, showing the principal chemical "
            "intermediates and the upstream inputs relevant to production costs and material "
            "availability.")


def _route_block(chemical: str, rows: Sequence[MarketPeriod], charts: dict[str, bytes],
                 routes: dict, narrative, picture, *, alone: bool) -> list[str]:
    """How this material is made. The diagram first, because a reader meets toluene there."""
    key = f"route|{chemical}"
    if key not in charts:
        return []
    name = _chain_title(chemical)
    wide = 13.6
    parts = [_named(name),
             _p(_image(f"rIdChart{charts_order(charts, key)}", wide,
                       round(wide * _ratio(charts[key]), 2), picture(), name),
                after=40, align="center", keep_next=True),
             _caption(route_caption(chemical))]
    note = _route_note(narrative, chemical, rows, alone=alone)
    if note:
        parts.append(_p(_run(note)))
    return parts


def conclusion_title(chemicals: Sequence[str]) -> str:
    """What the closing section is called.

    It used to name the manufacturing: "Combined Outlook for Flexible-Foam Manufacturing", from
    Charlie's CR-06 of 11 October 2026. Stefan, 12 October 2026: "I never said this model is for
    flex foam only." TDI and a polyether polyol go into flexible foam and into a good deal else,
    and an edition on pMDI and a polyester polyol goes somewhere different again. The conclusion
    is about the raw materials this edition covers, so it is named after them.
    """
    held = [display_chemical(one) for one in dict.fromkeys(str(one) for one in chemicals)]
    if len(held) < 2:
        return "What is expected next"
    return "Combined outlook for " + _joined_places(held)


def _window_words(row: MarketPeriod, period: Period) -> str:
    """The days this row was read over, written the way a caption writes them."""
    said = str(getattr(row, "scope_label", "") or "")
    if not said:
        return period.name
    return said.replace("over ", "", 1).replace("between ", "", 1).replace(" and ", " to ")


def _ratio(png: bytes) -> float:
    """A picture's own proportions, so a chart set in a column is never stretched."""
    from io import BytesIO

    try:
        from PIL import Image

        width, height = Image.open(BytesIO(png)).size
        return height / width if width else 0.72
    except Exception:
        return 0.72


# How wide one chart is when one, two or three are set across the page, in centimetres.
PANEL_CM = {1: 16.4, 2: 7.85, 3: 5.1}


def _chart_panel(row: MarketPeriod, charts: dict, period: Period, currency: str,
                 across: int, picture) -> tuple | None:
    """One market as a panel: its name and currency, the chart, and the days behind it."""
    key = f"{row.chemical}|{row.geography}"
    if key not in charts:
        return None
    place = getattr(row, "where", row.geography)
    terms = row.geography[len(place):].strip().strip("()") if row.geography != place else ""
    per = units.pair(currency)
    name = (f"{display_chemical(row.chemical)} ({place}, {terms}, {per})" if terms
            else f"{display_chemical(row.chemical)} ({place}, {per})")
    wide = PANEL_CM.get(across, PANEL_CM[3])
    high = round(wide * _ratio(charts[key]), 2)
    drawn = _image(f"rIdChart{charts_order(charts, key)}", wide, high, picture(), name)
    return (name, drawn, f"Observed: {_window_words(row, period)}")


MONITOR_HDR_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdMark" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
    'Target="media/htc_global.png"/>'
    "</Relationships>")


def _monitor_header(period_name: str, title: str = "PU Raw Material Price Monitor") -> str:
    """The running head: the HTC Global mark and what this document is, on every page.

    Stefan's reference layout, 12 October 2026. The asset is placed whole at its own proportions -
    "logo ratios cannot be changed" - with the strapline set beside it rather than cropped into it,
    and the page number sits here with the period rather than in the footer, so the foot of the
    page carries nothing but the copyright line.
    """
    mark = _image("rIdMark", 1.45, 1.45, 7, "HTC Global")
    left = (_p(mark, after=0))
    strap = (_p(_run("CHEMICAL MARKET", size=15, color=GREY, caps=True), after=0)
             + _p(_run("INTELLIGENCE", size=15, color=GREY, caps=True), after=0))
    right = (_p(_run(title, size=SMALL, bold=True, color=NAVY), after=20, align="right")
             + _p(_run(period_name + "   |   Page ", size=16, color=GREY)
                  + _field("PAGE"), after=0, align="right"))
    cells = (
        '<w:tc><w:tcPr><w:tcW w:w="900" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
        + left + '</w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="2600" w:type="dxa"/><w:vAlign w:val="center"/>'
        '<w:tcMar><w:left w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>' + strap + '</w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="6138" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
        + right + '</w:tc>')
    table = ('<w:tbl><w:tblPr><w:tblW w:w="9638" w:type="dxa"/>'
             '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:left w:val="nil"/>'
             '<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
             '<w:tblLayout w:type="fixed"/></w:tblPr>'
             '<w:tblGrid><w:gridCol w:w="900"/><w:gridCol w:w="2600"/><w:gridCol w:w="6138"/>'
             '</w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>' + cells + '</w:tr></w:tbl>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr ' + _NS + '>'
            + table + _p("", after=0, rule_bottom=RULE) + '</w:hdr>')


def _monitor_footer() -> str:
    """The copyright, and nothing else. The page number is in the running head."""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr ' + _NS + '>'
            + _p(_run(COPYRIGHT, size=16, color=GREY), after=0,
                 rule_bottom=None) + '</w:ftr>')


def _photo(key: str, photos: dict, width_cm: float, height_cm: float, pic_id: int) -> str:
    """A photograph from the reference bank, full width, as the layout opens a page with."""
    if key not in photos:
        return ""
    return _p(_image("rIdPhoto" + key, width_cm, height_cm, pic_id, "Reference photograph"),
              before=60, after=120, align="center", keep_lines=True)


def _next_period(period: Period) -> str:
    """The period the outlook is about: the one after the one being reported."""
    last = 12 if period.kind == "month" else 4
    if period.index >= last:
        return Period(period.year + 1, 1, period.kind).name
    return Period(period.year, period.index + 1, period.kind).name


def _about(supporting: Sequence[tuple[str, str]]) -> list[str]:
    """What the prices say about the chain, the coverage and the currency. Last, and quiet.

    Not a method section: nothing here says how anything is worked out. It says what was measured,
    over how much history, and in what currency, which is what a reader needs to weigh a figure.
    """
    rows = [(str(name), str(said).strip())
            for name, said in (supporting or ()) if str(said).strip()]
    if not rows:
        return []
    parts = [_section("About this report")]
    # The address sits here rather than on the cover. A reader looking for a person looks at the
    # back of a report, and a cover is for what the report is about.
    rows = list(rows) + [("Contact.", f"{CONTACT_NAME}, HTC Global. {CONTACT_EMAIL}")]
    for name, said in rows:
        parts.append(_p(_run(name + "  ", size=SMALL, bold=True, color=GREY)
                        + _run(said, size=SMALL, color=GREY), after=100))
    return parts

def _front(markets: Sequence[MarketPeriod], period: Period, today: date, currency: str,
           facts: dict, boxes: dict, charts: dict | None = None,
           photos: dict | None = None, picture=None) -> list[str]:
    """The first page, as Stefan's revised template of 12 October 2026 sets it.

    A photograph, the masthead block, then the edition in two sections: what happened, in the
    Observatory's own words, and then the four figures under "Price Movements". The charts
    belong to the materials and are set in their own sections further down.
    """
    charts, photos = dict(charts or {}), dict(photos or {})
    lead = markets[0] if markets else None
    chemicals = list(dict.fromkeys(display_chemical(row.chemical) for row in markets))
    # The places that carry a row, and only those: a title naming a country with nothing in the
    # period promises a section the edition does not have.
    places = list(dict.fromkeys(getattr(row, "where", row.geography) for row in markets))
    scope = _joined_places(chemicals) + (" - " + _joined_places(places) if places else "")
    kind = "Quarterly" if period.kind == "quarter" else "Monthly"
    out = [_photo("banner", photos, 17.0, 3.28, 8),
           _p(_run("PU Raw Material Price Monitor", size=TITLE, bold=True, color=NAVY),
              before=40, after=20),
           _p(_run(scope or (f"{display_chemical(lead.chemical)} in {lead.geography}"
                             if lead else ""), size=30, color=NAVY), after=20),
           _p(_run(f"{kind} Report - {period.name}", size=24, color=GREY), after=40),
           _p(_run(f"Edited by {CONTACT_NAME} - HTC Global", size=SMALL, color=GREY), after=60)]
    window = (f"{prose.day_in_words(period.first_day.isoformat())} - "
              f"{prose.day_in_words(period.last_day.isoformat())}")
    out.append(_window_rule(window))

    out.append(_section("Executive summary"))
    written = boxes.get("narrative")
    said = getattr(written, "headline", "") or boxes.get("headline", "")
    if said:
        out.append(_p(_run(said, size=26, bold=True, color=NAVY), before=40, after=80,
                      keep_next=True))
    opening = ([getattr(written, "opening", "")] if getattr(written, "opening", "") else
               prose.opening_paragraphs(markets, period, currency,
                                        condition=boxes.get("condition"),
                                        behind=boxes.get("behind"),
                                        wording=boxes.get("wording")))
    for line in opening:
        if line:
            out.append(_p(_run(line), after=80))

    out.append(_section("Price Movements"))
    out.append(_summary_tiles(facts, markets, period, currency))
    moved = [row for row in markets if row.change_percent is not None]
    if moved and not _all_full(moved):
        out.append(_p(_run(PARTIAL_NOTE, size=16, color=GREY), before=40, after=0))
    return out


def _market_block(chemical: str, rows: Sequence[MarketPeriod], period: Period,
                  charts: dict[str, bytes], behind: dict, condition, backgrounds: dict,
                  today: date, wording: dict, narrative, picture, *,
                  currency: str = "USD", page_break: bool = False) -> list[str]:
    """One material's own section: its chart, then the market behind it, then its figures.

    Stefan's revised template, 12 October 2026. The chart opens the section under the material's
    own heading; the market itself is read before the figures, because a reader who knows why a
    market moved reads the numbers differently from one who meets them cold.
    """
    from raw_material_forecasting import development_writer as writer

    parts = [_section(f"{display_chemical(chemical)} Price developments",
                      page_break=page_break)]
    for row in rows:
        panel = _chart_panel(row, charts, period, currency, 1, picture)
        if panel:
            parts.append(_panel_row([panel]))

    where = _joined_places([getattr(row, "where", row.geography) for row in rows])
    said = _written_for(narrative, "observed", chemical, rows)
    recorded = list(behind.get(chemical, ()))
    state = prose.condition_paragraph(condition, chemical, period)
    told: list[str] = []
    grounded = set()
    for row in rows:
        place = getattr(row, "where", row.geography)
        # The market behind the price is the place's, said once however many series it holds.
        if place in grounded:
            continue
        grounded.add(place)
        ground = backgrounds.get(f"{chemical}|{place}") or backgrounds.get(
            f"{chemical}|{row.geography}")
        written_here = _written_for(narrative, "background", chemical, [row])
        held = ([written_here] if written_here else
                [prose.background_paragraph(ground, period),
                 prose.coming_paragraph(ground, today),
                 prose.events_paragraph(ground, period)])
        told += [one for one in held if one]
    if said:
        told.append(said)
    elif recorded or state:
        if state:
            told.append(state)
        for entry in recorded:
            told.append(prose.behind_paragraph(entry, period,
                                               wording.get(writer.key_for(entry), "")))
    figures = [prose.market_paragraph(row, period) for row in rows
               if not _written_for(narrative, "observed", chemical, [row])]
    told += [one for one in figures if one]
    if told:
        parts.append(_heading(f"The market in {where}", 2))
        parts += [_p(_run(one)) for one in told if one]
    return parts


def _feedstock_panels(markets: Sequence[MarketPeriod], feedstocks: Sequence[MarketPeriod],
                      period: Period, charts: dict, notes: dict, currency: str,
                      picture, chemical: str = "", drawn: set | None = None) -> list[str]:
    """The priced steps under one material, and how each moved against it.

    Stefan's revised template, 12 October 2026: the feedstocks belong to the material they feed
    and sit inside its section, rather than in one list at the end. A feedstock that feeds two
    materials is drawn under the first of them and compared under each.
    """
    drawn = drawn if drawn is not None else set()
    mine = [row for row in feedstocks
            if not chemical or chemical in [name for name, _note in notes.get(row.chemical, ())]]
    if not mine:
        return []
    heading = "Supporting feedstock" + ("s" if len(mine) > 1 else "")
    parts = [_section(heading)]
    for row in mine:
        if row.chemical in drawn:
            continue
        drawn.add(row.chemical)
        panel = _chart_panel(row, charts, period, currency, 1, picture)
        if panel:
            parts.append(_panel_row([panel]))
    for row in mine:
        said = prose.market_paragraph(row, period)
        if said:
            parts.append(_p(_run(said)))
        for market in markets:
            if chemical and market.chemical != chemical:
                continue
            comparison = prose.pass_through_paragraph(market, row, period)
            if comparison:
                parts.append(_p(_run(comparison)))
    return parts


def _outlook(outlooks: dict, period: Period, narrative=None,
             chemicals: Sequence[str] = (), photos: dict | None = None,
             picture=None, not_forecast: dict | None = None) -> list[str]:
    """Where the markets are expected next, and what they mean together.

    One section, named for the period it is about. Stefan's revised template opens it with a
    photograph of what the materials are bought to make.
    """
    ahead = [one for one in (prose.outlook_paragraph(row, period)
                             for row in (outlooks or {}).values()) if one]
    written = str(getattr(narrative, "expectation", "") or "")
    # Without the Observatory's words the application's own expectation paragraph is the outlook
    # paragraphs joined, so printing both said every outlook twice.
    if not written:
        ahead = []
    said = written or prose.expectation_paragraph(outlooks, period)
    paragraphs = [one.strip() for one in str(said).split("\n") if one.strip()]
    # A series whose latest reading is too old for a forecast to land after this report is given
    # no outlook, and says how recent it is instead (Stefan, 14 October 2026).
    grouped: dict[tuple[str, str], list[str]] = {}
    for label, held in sorted((not_forecast or {}).items()):
        chemical, _bar, where = label.partition("|")
        month = str((held or {}).get("latest_month") or "")
        if month:
            grouped.setdefault((chemical, month), []).append(where)
    for (chemical, month), places in grouped.items():
        if len(places) == 1:
            paragraphs.append(f"{display_chemical(chemical)} in {places[0]} carries no outlook: its "
                              f"latest reading is for {month}.")
        else:
            paragraphs.append(f"{display_chemical(chemical)} in {_joined_places(places)} carry no "
                              f"outlook: the latest reading of each is for {month}.")
    if not ahead and not paragraphs:
        return []
    parts = [_section("Outlook for " + _next_period(period))]
    parts.append(_photo("band", dict(photos or {}), 16.47, 4.0, 9))
    if len(list(dict.fromkeys(str(one) for one in chemicals))) >= 2:
        parts.append(_heading(conclusion_title(chemicals), 2))
    for one in paragraphs:
        parts.append(_p(_run(one)))
    for one in ahead:
        parts.append(_p(_run(one)))
    return parts


def build(markets: Sequence[MarketPeriod], *, period: Period, charts: dict[str, bytes] | None = None,
          feedstocks: Sequence[MarketPeriod] = (),
          feedstock_notes: dict[str, Sequence[tuple[str, str]]] | None = None,
          behind: dict[str, Sequence] | None = None, condition=None, currency: str = "USD",
          outlooks: dict | None = None, routes: dict | None = None,
          backgrounds: dict | None = None, boxes: dict | None = None,
          wording: dict[str, str] | None = None,
          supporting: Sequence[tuple[str, str]] = (),
          photos: dict[str, bytes] | None = None,
          today: date | None = None, assets: Path | None = None,
          not_forecast: dict | None = None) -> bytes:
    """The whole document, in the order of Stefan's revised template of 12 October 2026.

    The edition opens with what happened and the figures that say it. Each material then has its
    own section: its chart, the market behind it, how it is made, and the priced steps under it.
    The outlook closes. Nothing is dropped - a market with nothing new in the period is still
    written up, because a gap in a report for a customer reads as a mistake.
    """
    today = today or date.today()
    markets = [row.with_period(period) for row in markets]
    feedstocks = [row.with_period(period) for row in feedstocks]
    charts = dict(charts or {})
    notes = dict(feedstock_notes or {})
    boxes = dict(boxes or {})
    photos = dict(photos or {})
    facts = headline(markets)
    narrative = boxes.get("narrative")
    picture = _pictures()
    chemicals = list(dict.fromkeys(row.chemical for row in markets))
    parts: list[str] = []
    parts += _front(markets, period, today, currency, facts, boxes, charts, photos, picture)
    drawn: set[str] = set()
    for chemical in chemicals:
        rows = [row for row in markets if row.chemical == chemical]
        parts += _market_block(chemical, rows, period, charts, dict(behind or {}), condition,
                               dict(backgrounds or {}), today, dict(wording or {}), narrative,
                               picture, currency=currency, page_break=False)
        parts += _regions(markets, period, chemical)
        parts += _route_block(chemical, rows, charts, dict(routes or {}), narrative, picture,
                              alone=len(chemicals) == 1)
        parts += _feedstock_panels(rows, feedstocks, period, charts, notes, currency, picture,
                                   chemical=chemical, drawn=drawn)
    left = [row for row in feedstocks if row.chemical not in drawn]
    if left:
        parts += _feedstock_panels(markets, left, period, charts, notes, currency, picture,
                                   drawn=drawn)
    parts += _outlook(dict(outlooks or {}), period, narrative, chemicals, photos, picture,
                      not_forecast=not_forecast)
    parts += _about(supporting)
    title = f"PU Raw Material Price Monitor - {period.name}"
    return _package("".join(parts), title, charts, assets or ASSETS, photos, period.name)


def _package(body: str, title: str, charts: dict[str, bytes], assets: Path,
             photos: dict[str, bytes] | None = None, period_name: str = "") -> bytes:
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
        photos = dict(photos or {})
        archive.writestr("word/_rels/document.xml.rels", _doc_rels(len(charts), photos))
        archive.writestr("word/_rels/header1.xml.rels", MONITOR_HDR_RELS)
        archive.writestr("word/styles.xml", _monitor_styles())
        archive.writestr("word/header1.xml", _monitor_header(period_name))
        archive.writestr("word/footer1.xml", _monitor_footer())
        archive.writestr("word/document.xml", document)
        archive.writestr("word/media/pi3_logo.png", (assets / PI3_LOGO.name).read_bytes())
        archive.writestr("word/media/htc_global.png", (assets / HTC_LOGO.name).read_bytes())
        for index, png in enumerate(charts.values()):
            archive.writestr(f"word/media/chart{index}.png", png)
        for key, blob in photos.items():
            archive.writestr(f"word/media/photo_{key}.png", blob)
    return buffer.getvalue()


def file_name(period: Period, today: date | None = None) -> str:
    today = today or date.today()
    tag = period.name.replace(" ", "-")
    return f"PU_Raw_Material_Price_Monitor_{tag}_{today:%Y%m%d}.docx"


def _inside(rows, period: Period):
    return [(day, value) for day, value in rows
            if period.first_day <= _as_day(day) <= period.last_day]


def _as_day(day):
    return day if isinstance(day, date) else date.fromisoformat(str(day)[:10])


# What a chart is drawn at for the column it is set in: pixels, and the width on the page it will
# be placed at. The second of these is what keeps the type the same height whichever column it is.
# Stefan, 12 October 2026: "we can make it more spacious by NOT putting graphs next to each
# other but below". One chart to a row, the full width of the page. The two- and three-across
# sizes are kept because a chart set in a column still has to be drawn for the column it is in.
PANEL_PIXELS = {1: (1600, 640), 2: (940, 470), 3: (740, 450)}


def chart_for(rows: Sequence[tuple[date, float]], currency: str, *, period: Period,
              title: str = "", market_name: str = "",
              forecast: Sequence[tuple[date, float]] = (),
              band: Sequence[tuple[date, float, float]] = (),
              across: int = 1) -> bytes:
    """One market, over the period and nothing else.

    Stefan, 10 October 2026: "when you show graphs, it is from the period only, not outside."
    The chart used to carry two years of history with the quarter squeezed into an eighth of the
    width, and then a second picture to rescue it. One market, one picture, the period's own days.

    The forecast is the one line that runs past the last day, because it is about the days after
    it and a reader buying in the next quarter is the person this edition is written for.
    """
    held = _inside(list(rows), period)
    if not held:
        return b""
    size = PANEL_PIXELS.get(across, PANEL_PIXELS[1])
    return price_chart_png(held, currency=currency, title=title, first_name=market_name,
                           forecast=list(forecast), band=list(band),
                           bridge=True, labels=True, size=size,
                           placed_cm=PANEL_CM.get(across, PANEL_CM[1]),
                           accent="orange", legend=True, months=across > 1)
