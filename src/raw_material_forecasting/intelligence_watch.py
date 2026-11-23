"""The folders HTC Global's own weekly intelligence lands in, watched so nobody has to remember.

Stefan, 8 October 2026: the briefing and the special report come out of a scheduled workflow every
week, and the intention is that they get loaded every week. Asking a person to remember to drag a
file onto a screen is how a weekly rhythm becomes a monthly one.

So the Import page looks in the folders those documents are written to and says what has arrived
that we do not hold. It proposes; it never stores on its own. Every row still goes past a person,
because the wording in a customer's report is a person's responsibility and not a folder's.

Two rules that come out of the documents themselves. Where a report exists as both Word and PDF,
the Word one is offered: the PDF loses the input-risk table to its own layout, which is how three
exposure levels went missing on 8 October. And a folder that is not where the configuration says
it is gets reported on the page, because a watch that silently finds nothing looks exactly like a
week when nothing was published.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

CONFIG = Path("data/config/intelligence_folders.json")
# These documents are named for the day they cover. That beats the file's timestamp, which moves
# when a folder is synced or a file is opened and saved again.
_NAMED_DAY = re.compile(r"(20\d{2})[-_ ]?(\d{2})[-_ ]?(\d{2})")
# Word first. A PDF of the same report is the same report, read worse.
PREFERRED_SUFFIX = (".docx", ".pdf")


@dataclass
class Watched:
    """One folder, and what is expected to land in it."""

    name: str
    kind: str                       # briefing / condition / monitor / any
    path: Path                      # the first place on the list that exists, or the first place
    patterns: tuple[str, ...] = ()
    missing: bool = False
    # What the documents in this folder may be used for. Stated by Stefan per report family, never
    # defaulted: the unattended round stores prices only from a folder that answers this, and the
    # engine refuses an import that does not carry one.
    rights_class: str = ""


@dataclass
class Arrival:
    """One document in a watched folder that the application does not hold."""

    file: Path
    folder: str
    kind: str
    written: date

    @property
    def name(self) -> str:
        return self.file.name

    @property
    def label(self) -> str:
        """The day and the document, with the day said once.

        Most of these are named for the day they cover, so prefixing the date produced
        "06 Oct 2026 - 2026-10-06 - Global PU Raw Material Price Monitor.docx".
        """
        stem = self.file.stem
        if _NAMED_DAY.match(stem):
            return re.sub(r"^20\d{2}[-_ ]?\d{2}[-_ ]?\d{2}\s*[-_]?\s*", "", stem).strip() \
                or stem
        return stem


def folders(config: Path = CONFIG, project: Path | None = None) -> list[Watched]:
    project = Path(project or Path.cwd()).resolve()
    if not Path(config).exists():
        return []
    try:
        raw = json.loads(Path(config).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    home = Path.home()
    out = []
    for row in raw.get("folders", []):
        places = row.get("paths") or ([row["path"]] if row.get("path") else [])
        resolved = []
        for place in places:
            where = str(place).replace("${PROJECT}", str(project)).replace("${HOME}", str(home))
            try:
                resolved.append(Path(where).resolve())
            except OSError:          # a path shape this operating system will not even parse
                continue
        if not resolved:
            continue
        # The first place that is actually there. The same application runs from a Windows desktop
        # and from a mounted copy, and the tree above the project is not the same in both.
        live = next((place for place in resolved if place.is_dir()), None)
        out.append(Watched(
            name=str(row.get("name", resolved[0].name)), kind=str(row.get("kind", "any")),
            path=live or resolved[0], patterns=tuple(row.get("patterns", ()) or ()),
            missing=live is None,
            rights_class=str(row.get("rights_class", "") or "")))
    return out


def _stem(name: str) -> str:
    """What makes two files the same report: the name without its suffix, loosely.

    Runs of space are collapsed, because a separator turning into a space is how these files
    differ. "2026-07-03_Strait_of_Hormuz..." and "2026-07-03 - Strait of Hormuz..." are the same
    report issued as a PDF and a Word file, and without collapsing they differ by the two spaces
    left where the " - " was - so five reports already held sat on the waiting list as though they
    had never been read.
    """
    loose = Path(name).stem.replace("_", " ").replace("-", " ").lower()
    return " ".join(loose.split())


def unread(watched: Sequence[Watched], *, data_dir, held: Sequence[str] = ()) -> list[Arrival]:
    """What is in the watched folders and has not been stored, decided by content.

    `arrivals` compares file names, which is why five reports already in the store sat on the
    waiting list - one report arrives as "2026-07-03_Strait_of_Hormuz..." and as
    "2026-07-03 - Strait of Hormuz...", and one week of the newsletter arrived twice with a "(1)"
    on the end.

    This asks each document for its content hash and the day it says it was written, remembers
    both against the file's size and modification time, and leaves out anything whose content has
    been stored. Reading 102 documents takes about a minute; reading them a second time takes no
    time at all.

    Stefan, 26 October 2026: "the file title is not decisive, neither is the saving date ... you
    need to explore the content."
    """
    from raw_material_forecasting import document_identity, document_intake, supply_conditions

    store = document_identity.load(data_dir)
    keep: dict[Any, tuple[int, Arrival]] = {}
    loose: list[Arrival] = []
    changed = False
    for arrival in arrivals(watched, held=held):
        before = len(store["cache"])
        identity = document_identity.identity_of(
            arrival.file, store,
            read_day=supply_conditions.day_of_document,
            open_document=document_intake.read)
        changed = changed or len(store["cache"]) != before
        if document_identity.already_read(identity, arrival.folder, store):
            continue
        if not identity.dated:
            # Nothing to match it on but its bytes, so it stands alone rather than being guessed at.
            loose.append(arrival)
            continue
        # One report, however many files carry it. The fuller copy is the one worth reading:
        # "APBA_Week_30_2026.html" holds 921 characters and its "(1)" twin holds the whole issue.
        key = (arrival.folder, identity.day)
        best = keep.get(key)
        if best is None or identity.chars > best[0]:
            keep[key] = (identity.chars, arrival)
    if changed:
        document_identity.save(store, data_dir)
    out = [arrival for _chars, arrival in keep.values()] + loose
    return sorted(out, key=lambda a: (a.written, a.name), reverse=True)


def arrivals(watched: Sequence[Watched], *, held: Sequence[str]) -> list[Arrival]:
    """Everything in the watched folders that is not already held, newest first.

    `held` is the document names the register and the condition file already carry. The comparison
    is on the loose stem, so the Word and the PDF of one report count as held once either is in.
    """
    already = {_stem(name) for name in held if str(name).strip()}
    found: dict[str, Arrival] = {}
    for folder in watched:
        if folder.missing:
            continue
        for pattern in folder.patterns or ("*",):
            for file in folder.path.glob(pattern):
                if not file.is_file() or file.name.startswith("~$"):
                    continue
                stem = _stem(file.name)
                if stem in already:
                    continue
                seen = found.get(stem)
                if seen is not None and _rank(file) >= _rank(seen.file):
                    continue
                found[stem] = Arrival(
                    file=file, folder=folder.name, kind=folder.kind, written=day_of(file))
    return sorted(found.values(), key=lambda row: (row.written, row.name), reverse=True)


def day_of(file: Path) -> date:
    """The day the document is named for, or failing that the day the file was last written."""
    found = _NAMED_DAY.search(file.name)
    if found:
        try:
            return date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
        except ValueError:
            pass
    return datetime.fromtimestamp(file.stat().st_mtime).date()


def _rank(file: Path) -> int:
    suffix = file.suffix.lower()
    return PREFERRED_SUFFIX.index(suffix) if suffix in PREFERRED_SUFFIX else len(PREFERRED_SUFFIX)


def held_documents(register_rows, conditions) -> list[str]:
    """Every document name the application already read something out of."""
    names = [str(getattr(row, "document", "") or "") for row in register_rows]
    names += [str(getattr(row, "document", "") or "") for row in conditions]
    return [name for name in names if name.strip()]


def describe(watched: Sequence[Watched], found: Sequence[Arrival]) -> str:
    live = [folder for folder in watched if not folder.missing]
    if not live:
        return "No folder is being watched."
    if not found:
        return (f"{len(live)} folders watched. Everything in them has been read."
                if len(live) > 1 else "The watched folder holds nothing new.")
    return (f"{len(found)} document{'' if len(found) == 1 else 's'} in "
            f"{len(live)} watched folder{'' if len(live) == 1 else 's'} "
            f"{'has' if len(found) == 1 else 'have'} not been read yet.")
