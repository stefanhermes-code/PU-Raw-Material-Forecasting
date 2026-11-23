"""One front door for any file somebody sends, whatever it is.

Stefan, 5 October 2026: "ANY type of document should be ingestible." So the import does not start
with a format, it starts with a file. This module works out what arrived and turns it into the two
things the rest of the import can work with - tables, and text - plus the page pictures where the
only way to read a document is to look at it.

Three rules hold the design together.

A file is always kept. Whether anything could be read out of it or not, the bytes are stored as
evidence, and a format we cannot read today becomes readable later without asking Stefan to find
the document again.

A reader that is not installed is said out loud. These libraries live in the Windows environment the
engine runs in, and a missing one must produce a sentence naming it rather than a stack trace or,
worse, an empty result that reads as "there was nothing in the document".

Nothing here decides what a number means. Tables come out as tables and text comes out as text;
turning either into a price happens in `price_import`, under a person's eye.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import PurePath

from raw_material_forecasting.price_import import Sheet

# What we recognise, by the end of the file name. Everything else is kept and reported.
KINDS: dict[str, tuple[str, ...]] = {
    "spreadsheet": (".xlsx", ".xlsm", ".xltx"),
    "delimited": (".csv", ".tsv"),
    "text": (".txt", ".md"),
    "pdf": (".pdf",),
    "word": (".docx",),
    "slides": (".pptx",),
    "web": (".html", ".htm", ".xhtml"),
    "email": (".eml",),
    "image": (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"),
    "opendocument": (".odt", ".ods"),
}
KIND_OF = {suffix: kind for kind, suffixes in KINDS.items() for suffix in suffixes}


@dataclass
class Document:
    """What came out of one file. Empty is a legitimate answer and `notes` says why."""

    name: str
    kind: str
    tables: list[Sheet] = field(default_factory=list)
    text: str = ""
    # Page pictures, for a scan or a photograph, so a reader that looks at pages can be given them.
    pages: list[bytes] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        return bool(self.tables or self.text.strip() or self.pages)


def kind_of(name: str) -> str:
    return KIND_OF.get(PurePath(name).suffix.lower(), "unknown")


def read(name: str, data: bytes) -> Document:
    """Everything this file will give up, without deciding what any of it means."""
    kind = kind_of(name)
    reader = {
        "spreadsheet": _read_spreadsheet,
        "delimited": _read_delimited,
        "text": _read_text,
        "pdf": _read_pdf,
        "word": _read_word,
        "slides": _read_slides,
        "web": _read_web,
        "email": _read_email,
        "image": _read_image,
        "opendocument": _read_opendocument,
    }.get(kind)
    if reader is None:
        return Document(name=name, kind="unknown", notes=[
            f"{PurePath(name).suffix or 'This file'} is not a format we read yet. "
            "It is kept whole, so it can be read once we can."])
    try:
        return reader(name, data)
    except ModuleNotFoundError as exc:
        return Document(name=name, kind=kind, notes=[
            f"The reader for this kind of file needs {exc.name}, which is not installed here. "
            "The file is kept."])
    except Exception as exc:                       # a bad file must not take the page down
        return Document(name=name, kind=kind, notes=[
            f"This file could not be opened: {type(exc).__name__}. It is kept as it arrived."])


def _read_delimited(name: str, data: bytes) -> Document:
    from raw_material_forecasting.price_import import read_csv

    sheet = read_csv(name, data)
    document = Document(name=name, kind="delimited", tables=[sheet] if sheet.rows else [])
    document.text = data.decode("utf-8-sig", errors="replace")
    if not sheet.rows:
        document.notes.append("No rows under the header.")
    return document


def _read_text(name: str, data: bytes) -> Document:
    return Document(name=name, kind="text", text=data.decode("utf-8-sig", errors="replace"))


def _read_spreadsheet(name: str, data: bytes) -> Document:
    from raw_material_forecasting.price_import import read_workbook

    sheets = read_workbook(name, data)
    document = Document(name=name, kind="spreadsheet", tables=sheets)
    if not sheets:
        document.notes.append("Every sheet in this workbook is empty.")
    return document


def _rows_to_sheet(name: str, rows: list[list[str]]) -> Sheet | None:
    rows = [[str(cell or "").strip() for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if len(rows) < 2:
        return None
    return Sheet(name=name, columns=tuple(rows[0]), rows=tuple(tuple(r) for r in rows[1:]))


def _read_pdf(name: str, data: bytes) -> Document:
    import pdfplumber

    document = Document(name=name, kind="pdf")
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as book:
        for number, page in enumerate(book.pages, start=1):
            parts.append(page.extract_text() or "")
            for index, table in enumerate(page.extract_tables() or [], start=1):
                sheet = _rows_to_sheet(f"{name}:page {number} table {index}", table)
                if sheet is not None:
                    document.tables.append(sheet)
    document.text = "\n".join(part for part in parts if part).strip()
    if not document.text and not document.tables:
        # A scan. The characters are pictures of characters, so somebody or something has to look.
        document.notes.append(
            "This PDF carries no text layer, so it is a scan. It needs the picture reader.")
    return document


def _read_word(name: str, data: bytes) -> Document:
    import docx

    book = docx.Document(io.BytesIO(data))
    document = Document(name=name, kind="word")
    document.text = "\n".join(p.text for p in book.paragraphs if p.text.strip())
    for index, table in enumerate(book.tables, start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        sheet = _rows_to_sheet(f"{name}:table {index}", rows)
        if sheet is not None:
            document.tables.append(sheet)
    return document


def _read_slides(name: str, data: bytes) -> Document:
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    document = Document(name=name, kind="slides")
    parts: list[str] = []
    for number, slide in enumerate(deck.slides, start=1):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                sheet = _rows_to_sheet(f"{name}:slide {number}", rows)
                if sheet is not None:
                    document.tables.append(sheet)
    document.text = "\n".join(parts).strip()
    return document


def _read_web(name: str, data: bytes) -> Document:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "html.parser")
    document = Document(name=name, kind="web")
    for index, table in enumerate(soup.find_all("table"), start=1):
        rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                for row in table.find_all("tr")]
        sheet = _rows_to_sheet(f"{name}:table {index}", rows)
        if sheet is not None:
            document.tables.append(sheet)
    for tag in soup(["script", "style"]):
        tag.decompose()
    document.text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return document


def _read_email(name: str, data: bytes) -> Document:
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(data)
    document = Document(name=name, kind="email")
    header = " | ".join(str(message.get(field_name, "")) for field_name in ("Date", "From", "Subject"))
    body = message.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        from bs4 import BeautifulSoup

        text = BeautifulSoup(text, "html.parser").get_text("\n", strip=True)
    document.text = (header + "\n\n" + text).strip()
    attachments = [part.get_filename() for part in message.iter_attachments()
                   if part.get_filename()]
    if attachments:
        # The price is usually in the attachment, not in the covering note.
        document.notes.append("This email carries " + ", ".join(attachments)
                              + ". Import each one as its own document.")
    return document


def _read_image(name: str, data: bytes) -> Document:
    document = Document(name=name, kind="image", pages=[data])
    try:
        import pytesseract
        from PIL import Image

        document.text = pytesseract.image_to_string(Image.open(io.BytesIO(data))).strip()
    except ModuleNotFoundError as exc:
        document.notes.append(f"Characters were not read: {exc.name} is not installed here.")
        return document
    except Exception as exc:
        document.notes.append(f"Characters were not read: {type(exc).__name__}.")
        return document
    if not document.text:
        document.notes.append("No characters were found in this picture.")
    else:
        # Reading characters off a picture mistakes a 6 for an 8 and loses a decimal point. The row
        # is a proposal like any other and every digit has to be looked at before it is confirmed.
        document.notes.append("Read from a picture. Check every digit before confirming.")
    return document


def _read_opendocument(name: str, data: bytes) -> Document:
    from odf import teletype, text as odf_text
    from odf.opendocument import load

    book = load(io.BytesIO(data))
    parts = [teletype.extractText(element)
             for element in book.getElementsByType(odf_text.P)]
    return Document(name=name, kind="opendocument",
                    text="\n".join(part for part in parts if part.strip()))


def describe(document: Document) -> str:
    """One sentence for the screen: what came out, and what did not."""
    if not document.readable:
        return document.notes[0] if document.notes else "Nothing could be read from this file."
    found = []
    if document.tables:
        found.append(f"{len(document.tables)} table{'s' if len(document.tables) > 1 else ''}")
    if document.text.strip():
        found.append(f"{len(document.text.split())} words of text")
    if document.pages:
        found.append(f"{len(document.pages)} page picture{'s' if len(document.pages) > 1 else ''}")
    return ", ".join(found) + "."
