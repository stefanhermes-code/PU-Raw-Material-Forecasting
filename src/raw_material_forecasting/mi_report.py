"""The Market Intelligence page as a branded Word report.

Stefan, 22 September 2026: a Word download on the Market Intelligence pages, branded, Calibri and
nothing below 10 pt, carrying everything the page carries, laid out as a professional report.

Built from the same objects the page is built from - the plant roster, the pipeline, the dated
developments and the Impact lines - so the document and the screen cannot say different things.
Written as plain Office Open XML, the way the forecast explainer is, so it needs no Word library.
"""
from __future__ import annotations

import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from raw_material_forecasting import market_intelligence as mi
from raw_material_forecasting.chemicals import display_chemical

ASSETS = Path("assets")
PI3_LOGO = ASSETS / "pi3_logo.png"
HTC_LOGO = ASSETS / "htc_global.png"

NAVY = "0B2A4A"
# The screen's unconfirmed chip, in ink. `.mi-unconfirmed` in `market_intelligence.PAGE_CSS`.
AMBER = "8A5A00"
BLUE = "1D4ED8"
GREY = "5C7184"
RULE = "C9D6E3"
SHADE = "F2F6FA"
FONT = "Calibri"
# Half-points. Nothing on the page is set below 10 pt.
BODY, SMALL, H2, H1, TITLE = 22, 20, 26, 32, 52
EMU_PER_CM = 360000


def _content_width() -> int:
    """A4 less 2 cm margins, in twentieths of a point. The width every table and figure is laid to."""
    return 9638


def _run(text: str, *, size: int = BODY, bold: bool = False, italic: bool = False,
         color: str | None = None, caps: bool = False) -> str:
    props = [f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>']
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if caps:
        props.append("<w:caps/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    props.append(f'<w:sz w:val="{max(size, SMALL)}"/><w:szCs w:val="{max(size, SMALL)}"/>')
    return f'<w:r><w:rPr>{"".join(props)}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _p(runs: str, *, before: int = 0, after: int = 120, align: str | None = None,
       rule_bottom: str | None = None, keep_next: bool = False, page_break: bool = False,
       keep_lines: bool = False) -> str:
    props = []
    if page_break:
        props.append("<w:pageBreakBefore/>")
    if keep_next:
        props.append("<w:keepNext/>")
    if keep_lines:
        props.append("<w:keepLines/>")
    if rule_bottom:
        props.append(f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="4" w:color="{rule_bottom}"/></w:pBdr>')
    props.append(f'<w:spacing w:before="{before}" w:after="{after}" w:line="276" w:lineRule="auto"/>')
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
    return f'<w:p><w:pPr>{"".join(props)}</w:pPr>{runs}</w:p>'


def _h1(text: str, *, page_break: bool = False) -> str:
    return _p(_run(text, size=H1, bold=True, color=NAVY), before=360, after=160, rule_bottom=BLUE,
              keep_next=True, keep_lines=True, page_break=page_break)


def _h2(text: str) -> str:
    return _p(_run(text, size=H2, bold=True, color=NAVY), before=240, after=100, keep_next=True,
              keep_lines=True)


def _image(rel_id: str, cm_wide: float, cm_high: float, pic_id: int, name: str) -> str:
    cx, cy = int(cm_wide * EMU_PER_CM), int(cm_high * EMU_PER_CM)
    return (
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{pic_id}" name="{name}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{pic_id}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


def _cell(content: str, width: int, *, fill: str | None = None, align: str | None = None) -> str:
    shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    body = content if content.startswith("<w:p>") else _p(content, after=0, align=align)
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}'
            '<w:tcMar><w:top w:w="70" w:type="dxa"/><w:bottom w:w="70" w:type="dxa"/></w:tcMar>'
            f'</w:tcPr>{body}</w:tc>')


def _table(headers: list[str], rows: list[list[str]], widths: list[int],
           numeric: set[int] | None = None, total_row: list[str] | None = None) -> str:
    """A report table: navy header repeated on every page, light rules, alternate row shading."""
    numeric = numeric or set()
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    borders = ('<w:tblBorders>'
               + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="{RULE}"/>'
                         for side in ("top", "bottom", "insideH"))
               + '<w:left w:val="nil"/><w:right w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>')
    head = ('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>'
            + "".join(_cell(_run(h, size=SMALL, bold=True, color="FFFFFF"), widths[i], fill=NAVY,
                            align="right" if i in numeric else None)
                      for i, h in enumerate(headers)) + "</w:tr>")
    body = ""
    for n, row in enumerate(rows):
        fill = SHADE if n % 2 else None
        body += ('<w:tr><w:trPr><w:cantSplit/></w:trPr>'
                 + "".join(_cell(value if value.startswith("<w:p>") else _run(value, size=SMALL),
                                 widths[i], fill=fill, align="right" if i in numeric else None)
                           for i, value in enumerate(row)) + "</w:tr>")
    if total_row:
        body += ('<w:tr><w:trPr><w:cantSplit/></w:trPr>'
                 + "".join(_cell(_run(value, size=SMALL, bold=True), widths[i], fill="E3EAF2",
                                 align="right" if i in numeric else None)
                           for i, value in enumerate(total_row)) + "</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>{borders}'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
            f"<w:tblGrid>{grid}</w:tblGrid>{head}{body}</w:tbl>" + _p("", after=120))


def _figures_table(figures: dict[str, str], width: int) -> str:
    """The page's headline figures as a band of labelled values."""
    items = list(figures.items())
    col = width // max(len(items), 1)
    labels = "".join(_cell(_run(k, size=SMALL, color=GREY, caps=True), col, fill=SHADE) for k, _ in items)
    values = "".join(_cell(_run(v, size=H2, bold=True, color=NAVY), col, fill=SHADE) for _, v in items)
    grid = "".join(f'<w:gridCol w:w="{col}"/>' for _ in items)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{col * len(items)}" w:type="dxa"/>'
            '<w:tblBorders><w:top w:val="nil"/><w:bottom w:val="nil"/><w:insideH w:val="nil"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="FFFFFF"/></w:tblBorders>'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
            f"<w:tblGrid>{grid}</w:tblGrid><w:tr>{labels}</w:tr><w:tr>{values}</w:tr></w:tbl>"
            + _p("", after=120))


def _status(value: str) -> str:
    text = str(value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:].lower() if text.isupper() else text


def _content(plants, pipeline, developments, horizons, chemical: str, geography: str,
             today: date, width: int, condition=None) -> str:
    name = display_chemical(chemical)
    place = mi._PLACE.get(str(geography or "China").strip(), "Country")
    parts = [
        _p(_image("rIdHtc", 2.6, 2.6, 1, "HTC Global"), after=200),
        _p(_run(f"{name} · {geography}", size=TITLE, bold=True, color=NAVY), after=40),
        _p(_run("Market Intelligence Report", size=H1, color=BLUE), after=60),
        _p(_run(today.strftime("%d %B %Y").lstrip("0"), size=BODY, color=GREY), after=240,
           rule_bottom=RULE),
    ]

    if condition is not None:
        # Above everything it explains, as on the screen. A reader told a price moved and never
        # told the ships had stopped has been given half the report.
        parts.append(_h1("Supply Chain Condition"))
        parts.append(_p(_run(condition.state.upper() + " · ", bold=True, color=NAVY)
                        + _run(f"{condition.event} · {mi.format_day(condition.reported)}",
                               color=NAVY), after=40))
        if condition.note:
            parts.append(_p(_run(condition.note, size=SMALL, color=GREY), after=120))
        mine = mi.exposures_for(condition, chemical)
        if mine:
            parts.append(_table(["Input", "Exposure", "Through what"],
                                [[e.group, e.level, e.mechanism] for e in mine],
                                [int(width * .30), int(width * .16), int(width * .54)]))

    parts.append(_h1("Summary"))
    for horizon in horizons:
        # The same words the screen puts beside the label: how many signals, how recent. A report
        # that states its conclusion without its age is the fault this page was fixed for.
        meta = mi.horizon_meta(horizon)
        parts.append(_p(_run(horizon.label, size=SMALL, bold=True, color=BLUE, caps=True)
                        + (_run(f"  ·  {meta}", size=SMALL, color=GREY) if meta else "")
                        + (_run(f"  ·  {horizon.aging}", size=SMALL, bold=True, color=BLUE)
                           if horizon.aging else ""),
                        before=160, after=40, keep_next=True))
        parts.append(_p(_run(horizon.headline, bold=True, color=NAVY), after=40 if horizon.body else 120,
                        keep_next=bool(horizon.body)))
        if horizon.body:
            parts.append(_p(_run(horizon.body), after=120))

    if plants:
        parts.append(_h1("Current Market Situation"))
        parts.append(_figures_table(mi.headline_figures(plants, pipeline, geography), width))
        shares = mi.owner_shares(plants)
        total = sum(p.kt for p in plants)
        rows = [[owner + (f" ({sites} sites)" if sites > 1 else ""), f"{kt:,}", f"{share:.1f}%"]
                for owner, kt, share, sites in shares]
        ranked = sum(kt for _o, kt, _s, _n in shares)
        if total > ranked:
            rows.append(["Other producers", f"{total - ranked:,}", f"{(total - ranked) / total * 100:.1f}%"])
        parts.append(_h2("Capacity by owner"))
        parts.append(_table(["Owner", "kt/yr", "Share"], rows,
                            [int(width * .6), int(width * .2), int(width * .2)], numeric={1, 2},
                            total_row=["Total", f"{total:,}", "100%"]))

        parts.append(_h1("Nameplate Capacity"))
        noted = any(p.note for p in plants)
        widths = ([int(width * .38), int(width * .18), int(width * .12), int(width * .32)] if noted
                  else [int(width * .56), int(width * .26), int(width * .18)])
        rows = [[p.plant, p.province, f"{p.kt:,}"] + ([p.note] if noted else []) for p in plants]
        parts.append(_table(["Plant", place, "kt/yr"] + (["Note"] if noted else []), rows, widths,
                            numeric={2}, total_row=["Total", "", f"{total:,}"] + ([""] if noted else [])))

    parts.append(_h1("New Capacity"))
    if pipeline:
        rows = [[item.owner, ", ".join(x for x in (item.site, item.province) if x), f"{item.kt:,}",
                 item.due.strftime("%B %Y") if item.due else "Not stated", _status(item.status)]
                for item in pipeline]
        parts.append(_table(["Owner", "Site", "kt/yr", "Due", "Status"], rows,
                            [int(width * .24), int(width * .28), int(width * .12), int(width * .18),
                             int(width * .18)], numeric={2}))
        for item in pipeline:
            if item.note:
                parts.append(_p(_run(f"{item.owner}: ", bold=True, size=SMALL)
                                + _run(item.note.rstrip(".") + ".", size=SMALL), after=80))
    else:
        parts.append(_p(_run(f"No new {mi._region(geography)} capacity is recorded as under way.")))

    parts.append(_h1("Market Signals"))
    parts.append(_h2("Signal Details"))
    if developments:
        rows = []
        for item in developments:
            # The same word the screen puts in front of the headline. A stated indication is
            # worth printing and is not worth printing as a fact.
            flag = (_run("Indication  \u00b7  ", size=SMALL, bold=True, color=AMBER, caps=True)
                    if str(getattr(item, "confidence", "")).upper() == "INDICATION" else "")
            signal = _p(flag + _run(item.development, size=SMALL, bold=True), after=0)
            if item.context:
                signal = signal + _p(_run(item.context, size=SMALL, color=GREY), after=0)
            # The one line a reader acts on. It is the most valuable thing HTC's own writing carries
            # and the screen draws it, so the document has to as well.
            if getattr(item, "implication", ""):
                signal = signal + _p(_run(item.implication, size=SMALL, color=NAVY), after=0)
            rows.append([mi.format_development_date(item), signal])
        parts.append(_table(["Date", "Signal"], rows, [int(width * .16), int(width * .84)]))
    else:
        parts.append(_p(_run("Nothing recorded yet.")))
    parts.append(_p(_run("Signals are collected worldwide and are not narrowed to the geography of "
                         "this report. What a producer does in one region sets what buyers in every "
                         "other one are quoted next.", size=SMALL, italic=True, color=GREY), before=120))
    return "".join(parts)


_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"')


def _header() -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {_NS}>'
            + _p(_image("rIdPi3", 2.1, 2.1 * 615 / 653, 10, "PI3 by PU ExpertCenter"), after=0, align="right",
                 rule_bottom=RULE)
            + "</w:hdr>")


def _field(code: str) -> str:
    size = f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}"/><w:color w:val="{GREY}"/><w:sz w:val="{SMALL}"/></w:rPr>'
    return (f'<w:r>{size}<w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r>{size}<w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
            f'<w:r>{size}<w:fldChar w:fldCharType="separate"/></w:r><w:r>{size}<w:t>1</w:t></w:r>'
            f'<w:r>{size}<w:fldChar w:fldCharType="end"/></w:r>')


def _footer(title: str) -> str:
    tab = (f'<w:r><w:rPr><w:sz w:val="{SMALL}"/></w:rPr><w:tab/></w:r>')
    body = (_run(f"PI³ by PU ExpertCenter  ·  HTC Global  ·  {title}", size=SMALL, color=GREY) + tab
            + _run("Page ", size=SMALL, color=GREY) + _field("PAGE") + _run(" of ", size=SMALL, color=GREY)
            + _field("NUMPAGES"))
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {_NS}>'
            '<w:p><w:pPr><w:pBdr><w:top w:val="single" w:sz="4" w:space="6" w:color="C9D6E3"/></w:pBdr>'
            '<w:tabs><w:tab w:val="right" w:pos="9638"/></w:tabs><w:spacing w:before="0" w:after="0"/></w:pPr>'
            + body + "</w:p></w:ftr>")


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>")
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>")
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rIdStyles" Type="{_R}/styles" Target="styles.xml"/>'
    f'<Relationship Id="rIdHeader" Type="{_R}/header" Target="header1.xml"/>'
    f'<Relationship Id="rIdFooter" Type="{_R}/footer" Target="footer1.xml"/>'
    f'<Relationship Id="rIdHtc" Type="{_R}/image" Target="media/htc_global.png"/>'
    "</Relationships>")
_HDR_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rIdPi3" Type="{_R}/image" Target="media/pi3_logo.png"/>'
    "</Relationships>")
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    f'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
    f'<w:sz w:val="{BODY}"/><w:szCs w:val="{BODY}"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    "</w:styles>")


def _core(title: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{escape(title)}</dc:title><dc:creator>PI³ by PU ExpertCenter</dc:creator>"
            "</cp:coreProperties>")


def report_file_name(chemical: str, geography: str, today: date | None = None) -> str:
    today = today or date.today()
    name = display_chemical(chemical).replace(" ", "_")
    return f"Market_Intelligence_{name}_{geography}_{today:%Y%m%d}.docx"


def market_intelligence_docx(plants, pipeline, signals=None, today: date | None = None, *,
                             chemical: str = mi.DEFAULT_MARKET[0], geography: str = mi.DEFAULT_MARKET[1],
                             directory: Path = mi.DATA_DIR, graph=None, assets: Path = ASSETS) -> bytes:
    """Word bytes for one market's intelligence report. The same content as `mi.page_blocks`."""
    today = today or date.today()
    developments = mi.load_developments(directory, signals=signals, chemical=chemical,
                                        geography=geography, graph=graph)
    condition = mi.latest_condition(directory)
    # The same lines as the screen: `current` leaves out a line standing on old evidence.
    horizons = mi.current(mi.impact(plants, pipeline, developments, today, geography, chemical,
                                    graph, condition))
    width = _content_width()
    title = f"{display_chemical(chemical)} · {geography} Market Intelligence"
    body = _content(plants, pipeline, developments, horizons, chemical, geography, today, width,
                    condition=condition)
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
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("word/_rels/header1.xml.rels", _HDR_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/header1.xml", _header())
        archive.writestr("word/footer1.xml", _footer(title))
        archive.writestr("word/document.xml", document)
        archive.writestr("word/media/pi3_logo.png", (assets / PI3_LOGO.name).read_bytes())
        archive.writestr("word/media/htc_global.png", (assets / HTC_LOGO.name).read_bytes())
    return buffer.getvalue()
