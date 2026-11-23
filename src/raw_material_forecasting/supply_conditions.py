"""The condition the supply chain is in, held as a dated record rather than as a document.

HTC Global writes a special report when something is happening to the chain itself - the Strait of
Hormuz series of July 2026 is the example. Stefan, 8 October 2026: that report is not a geopolitics
note that happens to mention chemicals. It grades each input group - TDI on high watch, MDI
medium-high, polyether and PO medium, catalysts and additives high - and it classifies the whole
chain on a five-step scale with the evidence that would move it either way.

So what the application keeps is not the document. It is the grade: one record per report, with the
day it was written, the state of the chain, and the exposure of each input. A market's section of a
customer report can then print the condition sitting over that market on the day, in one sentence,
instead of a reader being told a price moved and never learning that the ships had stopped.

The reader below proposes what it can find in the text and no more. These reports are written for
people, their tables do not come out of a PDF cleanly, and a level guessed wrong is worse than a
level left blank: whoever imports the report sets what the reader could not read.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

from raw_material_forecasting.briefing_reader import MONTHS, chemicals_in

FILE = "supply_conditions.json"
# The five states the special report classifies the chain on, weakest first.
STATES = ("Stable", "Tightening", "Constrained", "Disrupted", "Critical")
# How exposed one input group is, weakest first.
LEVELS = ("Low", "Medium", "Medium-high", "High")
_LEVEL_WORDS = {"low": "Low", "medium": "Medium", "medium-high": "Medium-high",
                "medium to high": "Medium-high", "high": "High"}

_DATE = re.compile(r"Report date\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I)
# How a report with no report-setting table writes its own date, on its first or second line:
#   "Date and Time: 01 May 2026, 16:29 (Bangkok time)"   -> day first
#   "Date and Time: May 29, 2026, 06:02 ICT"             -> month first
# Both are anchored on the label, so a date mentioned later in the prose cannot be mistaken for the
# day the report was written.
_DATE_LINE_DAY_FIRST = re.compile(
    r"Date and Time:\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I)
_DATE_LINE_MONTH_FIRST = re.compile(
    r"Date and Time:\s*([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I)
# "Generated on December 23, 2025" - how the APBA NewsBulletin dates itself.
_GENERATED_ON = re.compile(
    r"Generated on\s+([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I)
_STATE = re.compile(
    r"(?:classification (?:remains|is|should remain)|operating state is still|"
    r"state remains|remains)\s+(Stable|Tightening|Constrained|Disrupted|Critical)\b")
_ROW = re.compile(
    r"^(?P<group>[A-Za-z][A-Za-z ,/]{2,44}?)\s+"
    r"(?P<level>High|Medium to high|Medium-high|Medium|Low)\s+"
    r"(?:watch|operational risk)\b", re.M)
# The Word original says "High watch", "Medium-high cost", "High operational". The grade is the
# first word or two; the rest is the author telling you what kind of exposure it is.
_LEVEL_IN_CELL = re.compile(r"^\s*(Low|Medium[- ]to[- ]high|Medium-high|Medium|High)\b", re.I)
_DAY_IN_CELL = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


@dataclass
class Exposure:
    """One input group and how exposed the report says it is."""

    group: str
    level: str
    mechanism: str = ""
    chemicals: str = ""     # semicolon separated codes, where the group names markets we price

    @property
    def chemical_list(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.chemicals.split(";") if part.strip())


@dataclass
class Condition:
    """One special report, as a grade."""

    event: str = ""
    reported: str = ""
    state: str = ""
    note: str = ""
    document: str = ""
    exposures: list[Exposure] = field(default_factory=list)

    @property
    def day(self) -> date | None:
        try:
            return date.fromisoformat(str(self.reported)[:10])
        except ValueError:
            return None

    def exposure_of(self, chemical: str) -> Exposure | None:
        for exposure in self.exposures:
            if chemical in exposure.chemical_list:
                return exposure
        return None


# How a report writes its own date. ISO first, because that is what the file keeps; the rest are
# what the reports and the people typing them actually use.
_DATE_SHAPES: tuple[str, ...] = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y",
)


def iso_day(value: str) -> str:
    """The report date as ISO, or the text unchanged when no shape fits.

    Unchanged rather than dropped: a date nobody can read is still evidence that a report was
    filed, and `Condition.day` already answers None for it. Silently inventing a day would be
    worse than a row that sorts oddly.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    for shape in _DATE_SHAPES:
        try:
            return datetime.strptime(text[:len(shape) + 8].strip(), shape).date().isoformat()
        except ValueError:
            continue
    return text


def path(directory: Path) -> Path:
    return Path(directory) / FILE


def load(directory: Path) -> list[Condition]:
    file = path(directory)
    if not file.exists():
        return []
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    for row in raw if isinstance(raw, list) else []:
        exposures = [Exposure(**item) for item in row.get("exposures", []) if isinstance(item, dict)]
        out.append(Condition(event=row.get("event", ""), reported=row.get("reported", ""),
                             state=row.get("state", ""), note=row.get("note", ""),
                             document=row.get("document", ""), exposures=exposures))
    return out


def save(conditions: Sequence[Condition], directory: Path) -> Path:
    file = path(directory)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps([asdict(row) for row in conditions], indent=2), encoding="utf-8")
    return file


def append(new: Iterable[Condition], directory: Path) -> tuple[int, int]:
    held = load(directory)
    seen = {(row.event.strip().lower(), row.reported) for row in held}
    added = 0
    for condition in new:
        # The one door every path comes through, so the date is made ISO here rather than in each
        # of them. A non-ISO date makes `Condition.day` None and the reading invisible to
        # `in_period`; it also breaks the dedup key, because the same report filed twice in two
        # shapes is two rows.
        condition.reported = iso_day(condition.reported)
        key = (condition.event.strip().lower(), condition.reported)
        if key in seen:
            continue
        seen.add(key)
        held.append(condition)
        added += 1
    save(sorted(held, key=lambda row: (row.reported, row.event)), directory)
    return added, len(held)


def in_period(conditions: Sequence[Condition], first_day: date, last_day: date) -> Condition | None:
    """The last reading taken inside the period, or nothing.

    A condition is not carried forward. If the chain was graded in July and nobody graded it in
    September, the September report says nothing about it rather than repeating a stale state.
    """
    inside = [row for row in conditions
              if row.day is not None and first_day <= row.day <= last_day]
    return max(inside, key=lambda row: row.reported) if inside else None


_LABELLED_DAY_FIRST = re.compile(
    r"(?:Report date|Date and Time|Date)\s*(?:\([^)]{1,12}\))?\s*:?\s*"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I)
_LABELLED_MONTH_FIRST = re.compile(
    r"(?:Report date|Date and Time|Date|Generated on)\s*(?:\([^)]{1,12}\))?\s*:?\s*"
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I)
# "This report is current-dated to 31 July 2026" - the author saying it in a sentence.
_CURRENT_DATED = re.compile(
    r"current[- ]dated to\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I)


def day_of_document(document) -> str:
    """When the document says it was written, from its tables and its text.

    Tables first: a Word report keeps its date in the report-setting cell, and that cell is not in
    `document.text`. Returns "" where the document says nothing, and the caller must not invent
    one - the day a file was saved is not the day a report was written.
    """
    parts = []
    for table in list(getattr(document, "tables", ()) or ()):
        for row in _cells(table):
            parts.append(" ".join(str(cell) for cell in row))
    parts.append(str(getattr(document, "text", document) or ""))
    for chunk in parts:
        found = _day_in_text(chunk)
        if found:
            return found
    return ""


def _day_in_text(body: str) -> str:
    """The day the report says it was written, from whichever form it uses.

    The template's report-setting line first, then the "Date and Time:" line the untemplated
    updates open with. Returns "" rather than guessing: a report whose date cannot be read is
    filed by the person, not by the file system's idea of when it was saved.
    """
    for pattern in (_LABELLED_DAY_FIRST, _CURRENT_DATED):
        found = pattern.search(body)
        if found and MONTHS.get(found.group(2).lower()):
            return date(int(found.group(3)), MONTHS[found.group(2).lower()],
                        int(found.group(1))).isoformat()
    found = _LABELLED_MONTH_FIRST.search(body)
    if found and MONTHS.get(found.group(1).lower()):
        return date(int(found.group(3)), MONTHS[found.group(1).lower()],
                    int(found.group(2))).isoformat()
    return ""


def read_report(document, *, name: str = "", event: str = "") -> Condition:
    """What can be read out of a special report. Whatever it cannot read, it leaves empty.

    Stefan, 9 October 2026, looking at the form this screen put in front of him: "All that info is
    in the report, it is not good that I need to add it manually." He was right, and the reason it
    was not read is that this looked only at flat text. The Word original of a special report holds
    twenty-six tables - the report setting, the input-risk grid with its mechanisms, the
    classification grid and the final verdict - and all of it comes out cleanly. The PDF of the
    same report does not, which is why three exposure levels went missing on 8 October.

    So the tables are read first and the text is the fallback, for a PDF or for a report whose
    author did not use the template.
    """
    tables = list(getattr(document, "tables", ()) or ())
    body = str(getattr(document, "text", document) or "")
    if tables:
        from_tables = _from_tables(tables, name=name, event=event or _event_name(body, name))
        if from_tables.state or from_tables.exposures:
            return from_tables
    condition = Condition(event=event or _event_name(body, name), document=name)
    # The whole document, not only its running text: these reports keep the date in the
    # report-setting cell, and a cell is not in `document.text`.
    condition.reported = day_of_document(document) or condition.reported
    state = _STATE.search(body)
    if state:
        condition.state = state.group(1)
    seen: set[str] = set()
    for row in _ROW.finditer(body):
        group = " ".join(row.group("group").split()).strip(" ,")
        if group.lower() in seen or len(group) < 3:
            continue
        seen.add(group.lower())
        condition.exposures.append(Exposure(
            group=group, level=_LEVEL_WORDS[row.group("level").lower()],
            chemicals=";".join(chemicals_in(group))))
    return condition


def _cells(table) -> list[list[str]]:
    """A table as rows of plain strings, header included.

    `document_intake` hands tables over as a Sheet, which keeps the header apart from the rows.
    Reading only `rows` loses the header, and the header is how each of these tables is
    recognised - "Input group", "Classification", "Report setting".
    """
    rows = getattr(table, "rows", None)
    if rows is None:
        rows = table if isinstance(table, (list, tuple)) else None
    if rows is None:
        return []
    header = list(getattr(table, "columns", ()) or ())
    out = [header] if header else []
    out += [list(row) for row in rows]
    return [[" ".join(str(cell).split()) for cell in row] for row in out]


def _level_of(cell: str) -> str:
    found = _LEVEL_IN_CELL.match(str(cell or ""))
    if not found:
        return ""
    word = found.group(1).lower().replace(" to ", "-").replace("medium-to-high", "medium-high")
    return _LEVEL_WORDS.get(word, word.title())


def _from_tables(tables, *, name: str, event: str) -> Condition:
    """The report's own tables, which is where its author put all of this."""
    condition = Condition(event=event, document=name)
    for table in tables:
        rows = _cells(table)
        if not rows:
            continue
        header = [cell.lower() for cell in rows[0]]
        flat = " ".join(cell.lower() for row in rows for cell in row)

        # The cover table: when it was written, and the state it is in. Recognised by the labels
        # in its own left column rather than by its header, because the header is sometimes
        # "Field | Report setting" and sometimes the first pair of values.
        if len(rows[0]) == 2 and any(
                row[0].lower().startswith(("report date", "date and time", "current state"))
                for row in rows):
            for label, value in rows:
                low = label.lower()
                if ("date" in low and "update" not in low) and not condition.reported:
                    found = _DAY_IN_CELL.search(value)
                    month = MONTHS.get(found.group(2).lower()) if found else None
                    if month:
                        condition.reported = date(int(found.group(3)), month,
                                                  int(found.group(1))).isoformat()
                if "current state" in low and value:
                    # "Disrupted: renewed attacks ..." and "Disrupted, with controlled recovery
                    # signals" are both written. The state is the first word either way.
                    head = re.split(r"[:,.]", value, 1)
                    if head[0].strip().title() in STATES:
                        condition.state = head[0].strip().title()
                        rest = head[1].strip() if len(head) > 1 else ""
                        condition.note = (rest[:1].upper() + rest[1:]) or condition.note
            continue

        # The input-risk grid: one row per input group, with the mechanism the author wrote.
        if header and header[0].startswith("input group"):
            for row in rows[1:]:
                if len(row) < 2 or not row[0]:
                    continue
                level = _level_of(row[1])
                if not level:
                    continue
                condition.exposures.append(Exposure(
                    group=row[0], level=level,
                    mechanism=row[2] if len(row) > 2 else "",
                    chemicals=";".join(chemicals_in(row[0]))))
            continue

        # The classification grid: the row whose reason says it is the one that fits.
        if header and header[0].startswith("classification"):
            for row in rows[1:]:
                if len(row) < 2:
                    continue
                reason = row[1].lower()
                # "Best fit", "Best current fit", "Best current classification" - the author's
                # wording moves, the word Best does not.
                if row[0].title() in STATES and reason.startswith("best"):
                    condition.state = row[0].title()
            continue

        # The final verdict, stated in one cell at the end.
        if "pu supply chain state:" in flat:
            tail = flat.split("pu supply chain state:", 1)[1].strip().split()
            if tail and tail[0].strip(".").title() in STATES:
                condition.state = tail[0].strip(".").title()
    return condition


def _event_name(body: str, name: str) -> str:
    """The title, not the running footer, which also says "Special Report" on every page."""
    for line in body.splitlines():
        text = line.strip()
        if "|" in text or not text:
            continue
        if "special report" in text.lower() and len(text) < 80:
            return text
    return Path(name).stem.replace("_", " ") if name else ""
