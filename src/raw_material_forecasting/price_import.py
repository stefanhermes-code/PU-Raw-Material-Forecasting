"""Reading a price table out of a file somebody sent, without deciding anything on their behalf.

Stefan, 5 October 2026: "I am getting sometimes documents/images with pricing that are interesting
to have." This is the first half of the import: it turns a spreadsheet into candidate rows and says,
for every one of them, exactly what is missing before it could be stored. It writes nothing.

The rule the whole import is built on is that nothing enters the store that a person has not
confirmed. So this module never fills a blank. A document that does not say which Incoterm a price
is on leaves that field empty and the row is held back until somebody states it; a document whose
column header reads "Price" and nothing else does not acquire a currency because the other rows had
one. Guessing here would be indistinguishable, afterwards, from evidence.

What it does do is SUGGEST a mapping from the column headers it recognises, which is a different
thing: a suggestion is shown to the reader and applied only when they accept it.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from raw_material_forecasting import price_kinds

# What a price observation needs before it can be stored. Every one of these is a field the engine's
# identity resolver or the observation itself requires; a row missing any of them is not a price,
# it is a number.
REQUIRED = ("chemical", "geography", "basis", "currency", "unit", "observation_date", "price",
            "observation_type")
# Filled in by the reader from the import's own settings rather than from the file, where the file
# does not carry them. Still required; just not expected in a column.
FROM_SETTINGS = ("currency", "unit", "observation_type", "packaging")

# The four kinds of price, in `price_kinds`. This list used to hold six of the old seven - it was
# missing `seller_quote` - so a sheet could be imported as a kind the store knew and this did not.
OBSERVATION_TYPES = price_kinds.KINDS

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "chemical": ("chemical", "product", "material", "grade name", "produkt"),
    "geography": ("geography", "region", "market", "country", "area"),
    "basis": ("basis", "incoterm", "terms", "delivery", "inco"),
    "packaging": ("packaging", "pack", "container"),
    "currency": ("currency", "ccy", "valuta"),
    "unit": ("unit", "uom", "per"),
    "observation_date": ("date", "period", "month", "week", "as of", "datum"),
    "price": ("price", "value", "amount", "assessment", "prijs", "preis"),
    "observation_type": ("type", "price type", "observation type"),
}

_NUMBER = re.compile(r"^-?[\d.,\s']+$")
# A price may be written with its currency in front and its unit behind: "USD 2,350 / MT".
# Anything else carrying letters is prose, and prose is not a price. Stripping letters wherever
# they appeared turned "about 16k" into 16.0 - a number nobody wrote, which is the one thing
# this module exists to prevent. My own check caught it before it reached anything.
_CURRENCY_IN_FRONT = re.compile(r"^(?:[A-Z]{3}|[$\u20ac\u00a3\u00a5])\s*")
# The long spellings of a metric ton are deliberately absent. The application may not carry
# that word in any string - MT is the unit on every screen - and a reader pattern lives in
# the same files the rule is enforced over. A price suffix in the wild is /MT, /t or /kg.
_UNIT_BEHIND = re.compile(r"\s*/?\s*(?:MT|T|KG|TON|LB)\.?$", re.I)


@dataclass(frozen=True)
class Sheet:
    """A table as it arrived: the header row and the rows under it, nothing interpreted."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def column(self, header: str) -> int | None:
        try:
            return self.columns.index(header)
        except ValueError:
            return None


@dataclass
class ProposedRow:
    """One candidate price, with the reason it cannot be stored yet where that applies."""

    values: dict[str, Any]
    source_row: int
    missing: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing and not self.problems


def read_csv(name: str, data: bytes | str) -> Sheet:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    # Sniff the delimiter rather than assuming a comma. A European export is as likely to be
    # semicolon-separated, and a one-column sheet of "a;b;c" is a parse failure that looks like data.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    reader = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not reader:
        return Sheet(name=name, columns=(), rows=())
    header = tuple(str(cell).strip() for cell in reader[0])
    body = tuple(tuple(str(cell).strip() for cell in row) for row in reader[1:])
    return Sheet(name=name, columns=header, rows=body)


def read_workbook(name: str, data: bytes) -> list[Sheet]:
    """Every sheet in an xlsx, each as its own table. A workbook often holds one per market."""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out: list[Sheet] = []
    for worksheet in book.worksheets:
        rows = [[_cell(value) for value in row] for row in worksheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            continue
        out.append(Sheet(name=f"{name}:{worksheet.title}",
                         columns=tuple(rows[0]), rows=tuple(tuple(r) for r in rows[1:])))
    book.close()
    return out


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def suggest_mapping(columns: Sequence[str]) -> dict[str, str]:
    """Which column looks like which field. A suggestion, shown and accepted, never applied alone."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for field_name, hints in _HEADER_HINTS.items():
        for column in columns:
            if column in taken or not column:
                continue
            folded = column.strip().lower()
            if any(folded == hint or hint in folded for hint in hints):
                out[field_name] = column
                taken.add(column)
                break
    return out


def parse_number(text: str) -> float | None:
    """A price as written anywhere: 1,234.56, 1.234,56, 1 234,56, 1'234.56.

    Returns None rather than a guess when the thousands and decimal separators cannot be told apart.
    """
    raw = str(text or "").strip().replace(" ", " ")
    raw = _CURRENCY_IN_FRONT.sub("", raw)
    raw = _UNIT_BEHIND.sub("", raw).strip()
    if not raw or not _NUMBER.match(raw):
        return None
    cleaned = raw.replace(" ", "").replace("'", "")
    if "," in cleaned and "." in cleaned:
        # The rightmost of the two is the decimal separator; the other groups thousands.
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal, ".")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        # "1,234" is ambiguous on its own. Three digits after the separator is the thousands
        # grouping far more often than a three-decimal price, and a price with three decimals is
        # not a thing in this trade, so it reads as thousands.
        cleaned = (head + tail) if len(tail) == 3 else cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_day(text: str) -> str | None:
    """A date as written, to an ISO day. Ambiguous day/month orders are refused rather than picked."""
    raw = str(text or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m", "%Y/%m"):
        try:
            parsed = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        if pattern in ("%Y-%m", "%Y/%m"):
            return parsed.replace(day=1).date().isoformat()
        return parsed.date().isoformat()
    for pattern in ("%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return (parsed.replace(day=1) if pattern in ("%B %Y", "%b %Y") else parsed).date().isoformat()
    # A month with a position in it, as publishers write it: "Early Oct 2026", "mid-September
    # 2026", "late Aug 2026", "Oct 2026 opening". The 6 October 2026 Price Monitor carried two
    # TDI quotations dated this way and both were held back as undated. Early is the 1st, mid
    # the 15th, late the 25th; an opening quotation is the start of its month.
    lowered = raw.lower()
    position = next((day for word, day in (("early", 1), ("beginning", 1), ("start", 1),
                                           ("opening", 1), ("mid", 15), ("late", 25), ("end", 25))
                     if re.search(rf"\b{word}\b", lowered)), None)
    if position is not None:
        stripped = re.sub(r"\b(early|beginning|start|opening|mid|late|end)\b|\bof\b|-", " ", lowered)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        for pattern in ("%B %Y", "%b %Y"):
            try:
                parsed = datetime.strptime(stripped.title(), pattern)
            except ValueError:
                continue
            return parsed.replace(day=position).date().isoformat()
    return None


def propose(sheet: Sheet, mapping: dict[str, str], settings: dict[str, Any] | None = None
            ) -> list[ProposedRow]:
    """Candidate rows from one sheet, each with what is missing and what looks wrong.

    `mapping` is field -> column header, as the reader accepted it. `settings` carries what the
    import states for the whole file - currency, unit, packaging, what kind of price these are -
    for the fields a document usually leaves to the covering email.
    """
    settings = dict(settings or {})
    indexes = {field_name: sheet.column(column) for field_name, column in mapping.items()}
    out: list[ProposedRow] = []
    for number, row in enumerate(sheet.rows, start=2):
        values: dict[str, Any] = {key: value for key, value in settings.items() if value not in (None, "")}
        problems: list[str] = []
        for field_name, index in indexes.items():
            if index is None or index >= len(row):
                continue
            cell = row[index]
            if cell == "":
                continue
            if field_name == "price":
                number_value = parse_number(cell)
                if number_value is None:
                    problems.append(f"price {cell!r} could not be read as a number")
                    continue
                values["price"] = number_value
            elif field_name == "observation_date":
                day = parse_day(cell)
                if day is None:
                    problems.append(f"date {cell!r} could not be read")
                    continue
                values["observation_date"] = day
            else:
                values[field_name] = cell
        if values.get("observation_type"):
            # Folded, not refused. The store keeps four kinds and used to keep seven, so a sheet
            # written in the old words - "market_price", "contract_price" - is a sheet in the old
            # words and not a defect. A name nothing recognises is still reported, because at that
            # point the column is probably not a kind of price at all.
            stated = str(values["observation_type"]).strip()
            folded = price_kinds.kind_of(stated)
            if stated in price_kinds.KINDS or stated in price_kinds.WAS:
                values["observation_type"] = folded
            else:
                problems.append(f"{stated!r} is not a kind of price we store")
        if values.get("price") is not None and float(values["price"]) <= 0:
            problems.append("a price of zero or less is not a price")
        missing = tuple(name for name in REQUIRED if not values.get(name))
        # A row that carried nothing at all is an empty line, not a defect worth reporting.
        if not values.get("price") and len(missing) == len(REQUIRED) - len(
                [k for k in settings if settings.get(k)]):
            continue
        out.append(ProposedRow(values=values, source_row=number,
                               missing=missing, problems=tuple(problems)))
    return out


def summarise(rows: Iterable[ProposedRow]) -> dict[str, int]:
    rows = list(rows)
    return {"rows": len(rows),
            "ready": sum(1 for row in rows if row.ready),
            "held": sum(1 for row in rows if not row.ready)}
