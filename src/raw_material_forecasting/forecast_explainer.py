"""The forecast explainer: the banner, written out in plain words, as a Word document.

Every interpretation question that came back about the Forecast page was the same question in a
different form - what is the range, what does a percentage next to a month mean, is the forecast a
promise. The answers do not belong on the screen. Put them there and the banner becomes a page of
prose with four numbers in it; leave them off and the reader guesses. So they go in a document the
reader downloads once, reads at their own pace, and can forward to somebody who was not looking at
the screen.

The document is NOT static. It is written from the same BannerContent the banner is drawn from, so
the forecast, the months, the low and high and the hit rates in it are the ones on the screen at the
moment it was downloaded, and the sentences that quote those numbers are assembled around them. A
document with last quarter's figures in it and this quarter's date on the front is worse than no
document.

Nothing is asserted that the numbers do not show. "The range gets wider further out" and "the
percentages fall further out" are both written only when the figures do that; where they do not, the
document says what the figures are and stops. Untested horizons print a dash on the screen and are
left out of the prose here.

Every sentence says what something IS. There was a section headed "What this cannot do" and a run of
lines that defined the forecast by what it was not - not a quotation, not a promise, not a mistake.
That is a way of writing, not information, and a reader who has to assemble the meaning from the
negative has been handed work instead of an answer. The one thing worth keeping out of that section
- that the sharpest moves come from supply events and reach the number through the price - is now
stated plainly where the reader is being told how to use the forecast.

The unit is MT throughout, which is what the polyurethane trade writes and what Stefan asked for
on 16 September 2026. "Ton" is 907 kg in the United States and 1,016 kg in the United Kingdom, so a
price per "ton" is a price per one of three different things.

It is built with the standard library. A .docx is a zip of XML parts, and writing those four parts
directly keeps a Word document off the requirements list of an application that is installed on
machines behind a corporate proxy.
"""
from __future__ import annotations

from raw_material_forecasting import units
import re
import zipfile
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

NAVY = "1F3864"
GREY = "595959"
LINE = "D0D5DD"
SHADE = "F2F4F7"

def _money(currency: str, amount: float) -> str:
    return f"{currency} {amount:,.0f}".strip()


def _count(number: int) -> str:
    """Small numbers in words, the way the rest of the page writes them."""
    return {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}.get(number, str(number))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def file_name(content, *, today: date | None = None) -> str:
    """The name the download arrives under.

    Stamped with the FIRST month the forecast covers, not the day it was downloaded. Two documents
    pulled a fortnight apart carry the same figures and belong under the same name; one pulled on
    1 October and one on 30 September do not, and the month they forecast is what tells them apart.
    """
    stamp = _slug(content.month) if getattr(content, "month", "") not in ("", "-", None) \
        else (today or date.today()).strftime("%B_%Y")
    return f"{_slug(content.title)}_How_to_read_your_forecast_{stamp}.docx"


# --- the prose ------------------------------------------------------------------------------------

def _blocks(content, *, today: date | None = None) -> list[tuple]:
    """The document as a list of blocks, each a ("h1"|"p"|"lead"|"note"|"table", payload) pair.

    Kept separate from the XML so the wording can be read, and tested, without a zip file.
    """
    currency = content.currency or ""
    forecast = content.forecast_value
    rows = [row for row in content.rows if row.low_value is not None]
    month = content.month

    out: list[tuple] = []
    out.append(("p", "This page explains every number on the forecast, in plain words. Nothing "
                     "here is technical and you do not need a background in statistics to use it."))

    out.append(("h1", "What you are looking at"))
    out.append(("p", (f"The market price, our forecast for {month}, the change between them, "
                      if getattr(content, "previous_day", "") else
                      f"The last published price, our forecast for {month}, the change between them, ")
                     + "and how much room to leave around it."))
    out.append(("lead", f"{month}: {units.without_unit(content.forecast)} per {units.MT}"))
    out.append(("table", content))

    out.append(("h1", "The forecast number"))
    if forecast is not None:
        out.append(("p", f"{_money(currency, forecast)} is our best single estimate of what one {units.MT} "
                         f"will cost in {month}."))
    out.append(("p", "It is the month the decision in front of a buyer falls in: cover now, or "
                     "wait. The chart below the forecast runs further out, so the direction is "
                     "there to see."))

    # The direction, which is the part a buyer acts on.
    if content.move is not None and content.previous_value is not None:
        way = "above" if content.move >= 0 else "below"
        out.append(("h1", "The direction"))
        priced_on = getattr(content, "previous_day", "")
        if priced_on:
            # The reference is the market price, the same figure Current Market shows.
            out.append(("p", f"The forecast is {abs(content.move):.1f}% {way} the market price "
                             f"of {_money(currency, content.previous_value)} on {priced_on}, which "
                             "is the figure Current Market shows for this market. That change is "
                             "the part most buyers act on: it says which way the market is "
                             "heading between the price you can see and the month you are buying "
                             "for."))
            out.append(("p", f"The forecast is for the typical price across {month}, and the "
                             "market price is one day. The forecast starts from that price: it "
                             "is the last thing the method reads before it answers."))
        else:
            out.append(("p", f"The forecast is {abs(content.move):.1f}% {way} the last published price "
                             f"of {_money(currency, content.previous_value)} for "
                             f"{content.previous_month}. That change is the part most buyers act on: it "
                             "says which way the market is heading between the price you can see and "
                             "the month you are buying for."))
        # Only where there is a gap worth naming. On a market that publishes on time the forecast
        # starts from last month and saying so would be noise.
        gap = None if priced_on else content.gap_months
        if gap and gap > 1:
            out.append(("p", f"It reaches {_count(gap)} months past the last published price, "
                             f"because that is the gap you have to cover: the newest price for this "
                             f"market is {content.previous_month.split()[0]}, and you are buying "
                             f"for {month.split()[0]}."))

    out.append(("h1", "Low and High"))
    out.append(("p", "Each line is a level of certainty, and the band beside it is how much room "
                     "that certainty needs."))
    for row in rows:
        out.append(("p", f"{row.coverage}% certain means the price landed between "
                         f"{units.without_unit(row.low)} and {units.without_unit(row.high)} in "
                         f"{row.coverage} of every 100 past months."))
    if len(rows) > 1:
        out.append(("p", "The surer you want to be, the wider the band has to be. There is no "
                         "setting that gives you both."))
    out.append(("p", "These are measured, not assumed. We made this same forecast month by month "
                     "through the past few years, each time hiding everything that came afterwards, "
                     "and recorded how far each one landed from what the price actually did. The "
                     "bands are cut straight from those misses."))
    if any(row.drawable and abs(row.up) != abs(row.down) for row in rows):
        out.append(("p", "The two sides are rarely equal. Where the band reaches further up than "
                         "down, this market has surprised on the upside more often than the "
                         "downside, and the room you leave should follow it."))

    if forecast is not None and rows:
        first = rows[0]
        out.append(("h1", "A worked example"))
        out.append(("p", f"Say you need 100 {units.MT} in {month}. At the forecast of "
                         f"{_money(currency, forecast)} that is {_money(currency, forecast * 100)}."))
        out.append(("p", f"With {first.coverage}% certainty the real bill lands between "
                         f"{_money(currency, first.low_value * 100)} and "
                         f"{_money(currency, first.high_value * 100)}."))
        if len(rows) > 1:
            wider = rows[-1]
            out.append(("p", f"To be {wider.coverage}% certain, allow "
                             f"{_money(currency, wider.low_value * 100)} to "
                             f"{_money(currency, wider.high_value * 100)}."))
        out.append(("p", "So the forecast is a sound number to budget against, and the band tells "
                         "you how much room to leave."))

    out.append(("h1", "Where it stands"))
    out.append(("p", "The forecast is an estimate of where the market is heading, and the bands are "
                     "a track record."))
    out.append(("p", "The sharpest moves in this market come from plant outages and export swings. "
                     "They reach the forecast through the price, so it picks them up as they "
                     "land."))

    out.append(("h1", "How to use it"))
    out.append(("p", "Budget on the forecast number. Plan your exposure on the band, at whichever "
                     "of the two levels of certainty your position needs. Revisit both when the "
                     "market moves."))

    out.append(("note", f"Prices are per {units.MT}. Figures shown are those published on {content.as_at}, "
                        f"for {month}."))
    return out


# --- the file -------------------------------------------------------------------------------------

def _runs(text: str, *, bold=False, size=22, color="262626", italics=False) -> str:
    props = f'<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="{size}"/>' \
            f'<w:color w:val="{color}"/>'
    if bold:
        props += "<w:b/>"
    if italics:
        props += "<w:i/>"
    return f"<w:r><w:rPr>{props}</w:rPr><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r>"


def _para(text: str, *, bold=False, size=22, color="262626", after=140, before=0,
          italics=False, rule_top=False, rule_bottom=False, align=None) -> str:
    border = ""
    if rule_top:
        border += f'<w:top w:val="single" w:sz="6" w:space="8" w:color="{LINE}"/>'
    if rule_bottom:
        border += f'<w:bottom w:val="single" w:sz="6" w:space="8" w:color="{LINE}"/>'
    props = f'<w:spacing w:before="{before}" w:after="{after}"/>'
    if border:
        props += f"<w:pBdr>{border}</w:pBdr>"
    if align:
        props += f'<w:jc w:val="{align}"/>'
    body = _runs(text, bold=bold, size=size, color=color, italics=italics) if text else ""
    return f"<w:p><w:pPr>{props}</w:pPr>{body}</w:p>"


_COLS = (1900, 1500, 1500, 1700, 1700)


def _cell(text: str, width: int, *, bold=False, shade=None, right=False) -> str:
    shading = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}'
            f'<w:tcMar><w:top w:w="80" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/>'
            f'<w:left w:w="120" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar></w:tcPr>'
            + _para(text, bold=bold, size=20, after=0,
                    align="right" if right else None)
            + "</w:tc>")


def _table(content) -> str:
    widths = (2600, 1900, 1900)
    total = sum(widths)
    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="{LINE}"/>'
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
    head = ('<w:tr><w:trPr><w:tblHeader/></w:trPr>'
            + _cell("", widths[0], shade=SHADE)
            + _cell("Low", widths[1], bold=True, shade=SHADE, right=True)
            + _cell("High", widths[2], bold=True, shade=SHADE, right=True)
            + "</w:tr>")
    body = ""
    for row in content.rows:
        body += ("<w:tr>"
                 + _cell(f"{row.coverage}% certain", widths[0], bold=True)
                 + _cell(units.without_unit(row.low), widths[1], right=True)
                 + _cell(units.without_unit(row.high), widths[2], right=True)
                 + "</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>'
            f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr>"
            + "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{width}"/>' for width in widths) + "</w:tblGrid>"
            + head + body + "</w:tbl>")


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>")

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>")

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>")

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/></w:style>'
    "</w:styles>")


def explainer_docx(content, *, today: date | None = None) -> bytes:
    """The banner, explained, as Word bytes. Raises nothing; an empty banner gives a short document."""
    parts = [
        _para(content.title, bold=True, size=36, color=NAVY, after=40),
        _para("How to read your price forecast", size=26, color=GREY, after=320, rule_bottom=True),
    ]
    for kind, payload in _blocks(content, today=today):
        if kind == "h1":
            parts.append(_para(payload, bold=True, size=28, color=NAVY, before=260, after=120))
        elif kind == "lead":
            parts.append(_para(payload, bold=True, size=26, color=NAVY, before=80, after=200))
        elif kind == "table":
            parts.append(_table(payload))
            parts.append(_para("", after=0))
        elif kind == "note":
            parts.append(_para(payload, size=18, color=GREY, italics=True, before=400, rule_top=True))
        else:
            parts.append(_para(payload))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(parts) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1100" w:right="1100" w:bottom="1100" w:left="1100" '
        'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>")

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
