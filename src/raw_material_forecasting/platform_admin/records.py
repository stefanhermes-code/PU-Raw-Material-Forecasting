"""The standard way a record is maintained: Create, Edit / Delete, CSV / Excel import.

Stefan, 31 October 2026: *"On adding records we use a simple standard tab structure: Create,
Edit/Delete and CSV/Excel Import. We do this in all of my applications so I also want it here."*
It is the structure the Command Center already uses - `st.tabs(["Home", "Create", "Edit / Delete"])`
in companies, intake, leads and suspects - so the words and the widget are taken from there rather
than invented here.

Written once and used by all seven record pages. Seven copies of a three-tab layout would be seven
chances for one page to grow a Delete that means something different from the others', which is the
defect this audit keeps finding: one rule with several copies, and the one that runs is whichever
copy the reader happened to reach.

Two things this deliberately does not do:

  * **It does not delete.** Stefan's choice, the same day: switch the record off and keep the row.
    A company's entitlement, its users, its recipients and its deliveries all point at rows that
    have to stay readable, or the change history stops meaning anything. `models.switched_off`
    decides what off means for each kind, and `models.is_on` reads it back - one of each.
  * **It does not write the record itself.** Each page keeps its own form, because the fields of a
    delivery profile have nothing to do with the fields of a company. What is shared is the shape
    around the form, not the form.

The list of records stays below the tabs rather than inside a Home tab, because you read it while
you are editing.

WHAT "DOES NOT WORK" TURNED OUT TO BE (v1.2.123, 24 November 2026)
-------------------------------------------------------------------
Stefan, on Customers, Subscriptions and Users: *"it should show edit/delete but this does not
work, cannot edit anything."* Driven in a browser for the first time that day - until then these
pages were only ever read as source - and four things were wrong, all of them here and so on all
seven pages:

  * The Edit / Delete tab opened on a chooser reading "Create a new one" and nothing else. No
    field, no button, on the tab that is for editing. Now it opens on the first record.
  * A save that worked said "Saved" above a list still showing the old record, because the list
    had been read before the save. Now the page is drawn again and says what happened at the top.
  * A save without a change reason answered with an error and had already written the record,
    with no entry in the change history. The service now asks for who and why before it writes.
  * Delete was called "Switch off". It still does what Stefan chose on 31 October - the row is
    kept - and it is called Delete, which is the word on the tab.

A DELETED RECORD LEAVES THE PAGE (v1.2.124, the same day)
---------------------------------------------------------
Asked whether a deleted record should disappear from the list behind a tick box, Stefan: *"Yes,
hide them."* A delete that leaves the record in the list, in the chooser and in the download reads
as a delete that did not happen. So what `models.is_deleted` says is deleted is left out of all
three, and one tick box above the tabs - drawn only when there is something behind it, with the
count - puts it back, marked, so it can be read or brought back. The row is still kept.

Building it showed the chooser had been calling everything that was not in force "deleted": a
suspended customer, and an entitlement dated to start next month. Neither is. And a record so
mislabelled was refused its Delete button, as "already deleted". Both go by `is_deleted` now.

A TABLE, AND THE ROW CLICKED IS THE RECORD THAT OPENS (v1.2.125, the same day)
------------------------------------------------------------------------------
Stefan: *"Our standard way of working is that under Edit/Delete we see a clickable table, once
clicked the record opens to be edited (not before). Throughout the whole application use this."*

So the dropdown is gone and `clicked_record` is the one chooser: the table of records, in the
Edit / Delete tab, and nothing below it until a row is clicked. The list that used to sit under
the tabs is that table now - one list, not a list to read and a dropdown to choose from. After a
save or a delete the record closes and the table shows what changed.
"""
from __future__ import annotations

import io
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Iterable, Sequence

import pandas as pd
import streamlit as st

from .models import EntitlementScope, is_deleted, is_on, scope_to_dict, switched_off  # noqa: F401

CREATE, EDIT, IMPORT = "Create", "Edit / Delete", "CSV / Excel import"
TAB_LABELS = [CREATE, EDIT, IMPORT]

# A cell holding several values. Semicolons rather than commas, so a CSV written by Excel does not
# need the cell quoted and a person editing it by hand cannot break the row by forgetting to.
LIST_SEPARATOR = "; "
TRUE_WORDS = frozenset({"1", "y", "yes", "true", "t", "active", "on"})


# ----------------------------------------------------------------------------- cells
def cell_list(values: Iterable[str]) -> str:
    return LIST_SEPARATOR.join(sorted(str(v) for v in values or ()))


def read_list(value: Any) -> frozenset[str]:
    text = "" if value is None else str(value)
    return frozenset(part.strip() for part in text.replace(",", ";").split(";") if part.strip())


def read_flag(value: Any, *, default: bool = True) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return default if not text else text in TRUE_WORDS


def read_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def read_date(value: Any):
    """A day from a cell. Excel hands back a timestamp, a CSV hands back whatever was typed."""
    from datetime import date as _date

    text = read_text(value)
    if not text:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return pd.to_datetime(text).date()
    except Exception as exc:
        raise ValueError(f"{text!r} is not a date") from exc


def required(value: Any, what: str) -> str:
    text = read_text(value)
    if not text:
        raise ValueError(f"{what} is missing")
    return text


# ----------------------------------------------------------------------------- scope in a spreadsheet
# A scope is ten fields, seven of which are sets. Flattened one column per field and prefixed, so a
# reader of the sheet can see at a glance which columns are the entitlement and which are the
# record. `scope_to_dict` is the one that says what a scope's fields are; this follows it rather
# than listing them again.
SCOPE_PREFIX = "scope."
SCOPE_COLUMNS = tuple(f"{SCOPE_PREFIX}{name}" for name in scope_to_dict(EntitlementScope()))


def scope_cells(scope: EntitlementScope) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in scope_to_dict(scope).items():
        out[f"{SCOPE_PREFIX}{name}"] = cell_list(value) if isinstance(value, list) else value
    return out


def scope_from_cells(row: dict[str, Any]) -> EntitlementScope:
    return EntitlementScope(
        application_access=read_flag(row.get(f"{SCOPE_PREFIX}application_access"), default=False),
        features=read_list(row.get(f"{SCOPE_PREFIX}features")),
        chemicals=read_list(row.get(f"{SCOPE_PREFIX}chemicals")),
        geographies=read_list(row.get(f"{SCOPE_PREFIX}geographies")),
        incoterms=read_list(row.get(f"{SCOPE_PREFIX}incoterms")),
        packaging=read_list(row.get(f"{SCOPE_PREFIX}packaging")),
        forecast_horizon_months=int(float(read_text(row.get(f"{SCOPE_PREFIX}forecast_horizon_months")) or 0)),
        report_formats=read_list(row.get(f"{SCOPE_PREFIX}report_formats")),
        delivery_frequencies=read_list(row.get(f"{SCOPE_PREFIX}delivery_frequencies")),
        delivery_channels=read_list(row.get(f"{SCOPE_PREFIX}delivery_channels")),
    )


# ----------------------------------------------------------------------------- the kind of record
@dataclass(frozen=True)
class RecordKind:
    """What one page maintains, in the terms the three tabs need.

    `save` is the page's own service call, so an import goes through exactly the checks a person
    typing into the form goes through - an entitlement scope wider than the company's is refused
    whether it arrives by keyboard or by spreadsheet.
    """
    noun: str                                   # "company", "user" - used in the words on screen
    id_of: Callable[[Any], str]
    name_of: Callable[[Any], str]
    to_row: Callable[[Any], dict[str, Any]]     # a record -> one spreadsheet row
    from_row: Callable[[dict[str, Any]], Any]   # one spreadsheet row -> a record
    save: Callable[[Any, str, str], Any]        # (record, actor, reason) -> saved
    # The word for a record `is_deleted` is true of. An entitlement that was deleted and one that
    # ran out are the same row, so those two kinds say "ended".
    gone: str = "deleted"

    @property
    def plural(self) -> str:
        return f"{self.noun[:-1]}ies" if self.noun.endswith("y") else f"{self.noun}s"

    @property
    def show_gone(self) -> str:
        """The words on the tick box, which other messages point the reader to."""
        return f"Show {self.gone} {self.plural}"


# ----------------------------------------------------------------------------- shared small parts
def reason_fields(prefix: str) -> tuple[str, str]:
    actor = st.text_input("Changed by", value="HTC Platform Admin", key=f"{prefix}_actor")
    reason = st.text_input("Change reason (required)", key=f"{prefix}_reason",
                           placeholder="Why this change is being made - kept in the change history")
    return actor, reason


# What the last action did, said once at the top of the page it is drawn on next.
FLASH_KEY = "platform_admin_flash"
# {a table's key: the id of the record open under it}. Written by a click: see `clicked_record`.
OPEN_KEY = "platform_admin_open"
# {a table's key: the table it was clicked in}. See `clicked_record` for why that is kept.
OPEN_TABLE_KEY = "platform_admin_open_in"
# How many writes this session has made. Part of every form's keys: see `form_mark`.
WRITES_KEY = "platform_admin_writes"


def form_mark(record_id: str | None) -> str:
    """What a form's keys carry: the record it is for, and how many writes came before it.

    A widget keeps its value under its key, in the session AND in the browser. A form whose keys
    were only the record's id was therefore the same widgets after a write as before it, and went
    on showing what had been typed or what had been stored, whichever the browser had last: a
    customer deleted a moment earlier still read ACTIVE above the Delete button, and Save would
    have written that back (seen in the browser, 24 November 2026; clearing the session's copy did
    not help, because the browser keeps its own).

    So a write changes the keys. Every form on the page is then new widgets, drawn from the store,
    and the Create form is empty again for the same reason.
    """
    drawn = int(st.session_state.get(WRITES_KEY, 0))
    base = record_id or "new"
    return base if not drawn else f"{base}.{drawn}"


def show_flash() -> None:
    message = st.session_state.pop(FLASH_KEY, None)
    if message:
        st.success(message)
        # And where the reader is. A user's form is two screens long and its Save button is at the
        # bottom, so a message only at the top of the page was a message nobody saw.
        st.toast(message)


def safe_action(action, *, done: str = "Saved."):
    """Run a write. Refused: say why, and leave the form as typed. Done: draw the page again.

    Drawing it again is the point. Every one of these pages reads its records at the top and draws
    its list at the bottom, so a write made in between left the old record on the screen under the
    word "Saved" - which reads as a save that did not take. The message is carried over the redraw
    and shown at the top (`show_flash`), every form comes back as new widgets (`form_mark`), and
    the record that was open closes, so what is on the screen is the table with the change in it.
    """
    from .service import PlatformAdminError

    try:
        action()
    except (PlatformAdminError, ValueError) as exc:
        st.error(str(exc))
        return None
    st.session_state[FLASH_KEY] = done
    st.session_state[OPEN_KEY] = {}            # the record closes; the table shows what changed
    st.session_state[OPEN_TABLE_KEY] = {}
    st.session_state[WRITES_KEY] = int(st.session_state.get(WRITES_KEY, 0)) + 1     # see `form_mark`
    st.rerun()


def clicked_record(rows: Sequence[dict[str, Any]], ids: Sequence[str], *, key: str) -> str | None:
    """The standard chooser: a table, and the row clicked is the record that opens. Not before.

    Stefan, 24 November 2026: *"Our standard way of working is that under Edit/Delete we see a
    clickable table, once clicked the record opens to be edited (not before). Throughout the whole
    application use this."* Written once here for every place this application chooses a record
    to change.

    Returns the id of the record that is open, or nothing. `rows` and `ids` run in step.

    A click ANYWHERE on the row opens it. The table is told to answer a click on a cell, and the
    row of that cell is the record. Told to answer rows instead - which is what the Command
    Center's grids do - it grows a column of small tick boxes and only a tick opens anything: a
    click on the customer's name highlighted the name and did nothing (seen in the browser the
    day this was built).

    What is open is kept by id, under `OPEN_KEY`, and not as the row number the table hands back.
    A row number is only right for the list it was clicked in: tick "Show deleted" and the second
    row is a different record. So the table's key carries the list it was drawn from and the
    number of writes made, a changed list is a new table with nothing clicked in it, and a record
    clicked in the old one is no longer open.
    """
    ids = [str(one) for one in ids]
    writes = int(st.session_state.get(WRITES_KEY, 0))
    table_key = f"{key}_table.{writes}.{zlib.crc32('|'.join(ids).encode('utf-8')):x}"
    opened = st.session_state.get(OPEN_KEY, {}).get(key)
    clicked_in = st.session_state.get(OPEN_TABLE_KEY, {}).get(key)
    if opened not in ids or clicked_in not in (None, table_key):
        opened = None

    def clicked() -> None:
        cells = st.session_state[table_key]["selection"].get("cells", [])
        held = dict(st.session_state.get(OPEN_KEY, {}))
        tables = dict(st.session_state.get(OPEN_TABLE_KEY, {}))
        if cells and int(cells[0][0]) < len(ids):
            held[key], tables[key] = ids[int(cells[0][0])], table_key
        else:                                   # the selection was cleared: the record closes
            held.pop(key, None)
            tables.pop(key, None)
        st.session_state[OPEN_KEY], st.session_state[OPEN_TABLE_KEY] = held, tables

    st.caption("Click a row to open it." if opened is None
               else "Click another row to open that one.")
    st.dataframe(pd.DataFrame(list(rows)), width="stretch", hide_index=True, key=table_key,
                 on_select=clicked, selection_mode="single-cell")
    return opened


# ----------------------------------------------------------------------------- the three tabs
def _switch_off(kind: RecordKind, record: Any, *, deactivate, key: str) -> None:
    """Delete, as Stefan defined it: the record stops applying and the row stays."""
    now = datetime.now(timezone.utc)
    st.divider()
    st.markdown(f"**Delete this {kind.noun}**")
    # `is_deleted`, not "not in force": a suspended customer and an entitlement that has not
    # started yet are both off, and both were refused this button as "already deleted".
    if is_deleted(record, at=now):
        st.caption(f"This {kind.noun} is already {kind.gone}: it is switched off and no longer "
                   f"applies. Edit it above to bring it back.")
        return
    st.caption(
        f"A deleted {kind.noun} is switched off from now and no longer applies. The row is kept, "
        f"with everything that points at it, so the change history stays readable - and editing "
        f"it brings it back.")
    mark = form_mark("off")
    with st.form(f"{key}_{mark}_form"):
        actor, reason = reason_fields(f"{key}_{mark}")
        if st.form_submit_button(f"Delete this {kind.noun}", type="secondary"):
            safe_action(lambda: deactivate(record, actor=actor, reason=reason),
                        done=f"Deleted: {kind.name_of(record)} is switched off and kept in the "
                             f"change history. Tick '{kind.show_gone}' to see it.")


def _sheet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="records")
    return buffer.getvalue()


def _read_upload(upload) -> pd.DataFrame | None:
    name = str(getattr(upload, "name", "")).lower()
    if name.endswith((".xlsx", ".xlsm")):
        return pd.read_excel(upload)
    return pd.read_csv(upload)


def _spreadsheet(kind: RecordKind, records: Sequence[Any], *, key: str,
                 held_ids: frozenset[str] | None = None, left_out: int = 0) -> None:
    """Export what is held, edit it anywhere, bring it back.

    An import updates a row whose id already exists and creates one whose id does not - Stefan's
    choice, 31 October 2026 - so the sheet is a way of correcting twenty records at once and not
    only of loading the first twenty. Every row written goes through the same service call the form
    uses, so a spreadsheet cannot grant what a person could not.

    `records` is what the page shows, so a download is what is on the screen. `held_ids` is every
    id in the store, shown or not: a row for a deleted record is an update, and saying "create"
    for it would be wrong about what the upload is about to do.
    """
    held = pd.DataFrame([kind.to_row(record) for record in records])
    st.caption(
        f"Download what is held, change it in Excel, upload it back. A row whose "
        f"{kind.noun} ID already exists is updated; one whose ID is new is created."
        + (f" The download leaves out the {left_out} {kind.gone}; tick '{kind.show_gone}' above "
           f"to include them." if left_out else ""))
    if not held.empty:
        left, right = st.columns(2)
        left.download_button("Download as CSV", held.to_csv(index=False).encode("utf-8"),
                             file_name=f"{kind.plural}.csv", mime="text/csv",
                             key=f"{key}_csv", width="stretch")
        right.download_button(
            "Download as Excel", _sheet_bytes(held), file_name=f"{kind.plural}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key}_xlsx", width="stretch")
    else:
        st.info(f"No {kind.noun} is shown, so there is nothing to download. An upload still "
                f"works: its column names are what a download would have written.")

    upload = st.file_uploader("CSV or Excel file", type=["csv", "xlsx", "xlsm"], key=f"{key}_upload")
    if upload is None:
        return
    try:
        frame = _read_upload(upload)
    except Exception as exc:                       # a file a person chose by hand, in any state
        st.error(f"That file could not be read: {exc}")
        return
    if frame is None or frame.empty:
        st.warning("That file has no rows.")
        return

    known = held_ids if held_ids is not None else {kind.id_of(record) for record in records}
    rows = [{str(k): v for k, v in row.items()} for row in frame.to_dict(orient="records")]
    # Said before anything is written, because "20 rows imported" after the event is not an answer
    # to "what did that do to my customers?".
    planned, unreadable = [], []
    for number, row in enumerate(rows, start=2):   # row 1 is the header, as the person sees it
        try:
            record = kind.from_row(row)
        except Exception as exc:
            unreadable.append(f"row {number}: {exc}")
            continue
        planned.append((number, "Update" if kind.id_of(record) in known else "Create", record))
    creating = sum(1 for _, verb, _ in planned if verb == "Create")
    st.write(f"**{creating} to create, {len(planned) - creating} to update**"
             + (f", {len(unreadable)} that could not be read" if unreadable else ""))
    if unreadable:
        st.error("\n\n".join(unreadable[:10]))
    if not planned:
        return
    st.dataframe(pd.DataFrame([
        {"Row": number, "What": verb, kind.noun.title(): kind.name_of(record)}
        for number, verb, record in planned]), width="stretch", hide_index=True)
    with st.form(f"{key}_import_form"):
        actor, reason = reason_fields(f"{key}_import")
        if st.form_submit_button(f"Write these {len(planned)} rows", type="primary"):
            written, refused = 0, []
            for number, _verb, record in planned:
                try:
                    kind.save(record, actor, reason or f"imported from {upload.name}")
                    written += 1
                except Exception as exc:
                    refused.append(f"row {number}: {exc}")
            if written:
                st.success(f"{written} written.")
            if refused:
                # Each refusal names its row, so the sheet can be corrected and sent again. The
                # rows that went in are in; nothing is rolled back, because a half-written import
                # that reports nothing is worse than one that says exactly what it did.
                st.error("These were refused:\n\n" + "\n\n".join(refused[:10]))


def shown_records(kind: RecordKind, records: Sequence[Any], *, key: str) -> list[Any]:
    """What the page shows: everything not deleted, and the deleted too once the box is ticked.

    One box for the chooser, the list and the download, because three boxes would be three chances
    for a page to show a record in one place that it hides in another. It is drawn only when
    something is behind it, and says how many, so a page with nothing deleted looks as it did.
    """
    now = datetime.now(timezone.utc)
    live = [record for record in records if not is_deleted(record, at=now)]
    if len(live) == len(records):
        return live
    show = st.checkbox(
        f"{kind.show_gone} ({len(records) - len(live)})", key=f"{key}_show_gone",
        help=f"A {kind.gone} {kind.noun} is switched off and kept. Tick this to see it in the "
             f"chooser, the list and the download, and to bring it back by editing it.")
    return list(records) if show else live


def record_tabs(kind: RecordKind, records: Sequence[Any], draw_form, *, deactivate, key: str,
                listed: Callable[[Any], dict[str, Any]] | None = None,
                after: Callable[[Any], None] | None = None) -> list[Any]:
    """Create / Edit or Delete / CSV or Excel import, in that order, on every record page.

    `draw_form(None)` draws the empty form, `draw_form(record)` the same form filled in. It is the
    page's own, because the fields differ; the three tabs around it do not.

    Edit / Delete is the table of records (`clicked_record`), and a record's form is drawn only
    once its row is clicked. `listed(record)` is that record's row, in the page's own columns; a
    spreadsheet row is used when a page gives none. `after(record)` draws what else belongs to an
    open record between its form and its Delete - a user's sign-in password.

    Returns the records the page shows (`shown_records`).

    `st.tabs` runs the body of every tab on every render, which is why the navigation above this
    does not use it. Here it costs nothing: the records were read once before these tabs, and the
    three panels only draw widgets over what is already in hand.
    """
    shown = shown_records(kind, records, key=key)
    # Keyed, and told to the server. An unkeyed tab bar is remembered by the browser alone and
    # only for as long as nothing above it on the page changes - so the message a save puts at the
    # top of the page threw the reader from Edit / Delete back to Create, on the record they had
    # just saved (seen in the browser, 24 November 2026; a test that only reads widgets cannot).
    create_tab, edit_tab, sheet_tab = st.tabs(TAB_LABELS, key=f"{key}_tabs", on_change="rerun")
    with create_tab:
        draw_form(None)
    with edit_tab:
        if not records:
            st.info(f"No {kind.noun} is held yet. Create one first.")
        elif not shown:
            st.info(f"Every {kind.noun} held here is {kind.gone}. Tick '{kind.show_gone}' above "
                    f"to see one or bring it back, or create a new one.")
        else:
            now = datetime.now(timezone.utc)
            some_gone = any(is_deleted(record, at=now) for record in shown)

            def row(record: Any) -> dict[str, Any]:
                cells = dict((listed or kind.to_row)(record))
                if some_gone:                    # only once the box is ticked, so only then a column
                    cells[kind.gone.title()] = "yes" if is_deleted(record, at=now) else ""
                return cells

            opened = clicked_record([row(record) for record in shown],
                                    [kind.id_of(record) for record in shown], key=key)
            chosen = next((record for record in shown if str(kind.id_of(record)) == opened), None)
            if chosen is not None:
                # Deleted is one thing and not in force is another: see `models.is_deleted`.
                state = (kind.gone if is_deleted(chosen, at=now)
                         else "" if is_on(chosen, at=now) else "not active")
                st.markdown(f"#### {kind.name_of(chosen)}" + (f"  -  {state}" if state else ""))
                draw_form(chosen)
                if after is not None:
                    after(chosen)
                _switch_off(kind, chosen, deactivate=deactivate, key=key)
    with sheet_tab:
        _spreadsheet(kind, shown, key=key, left_out=len(records) - len(shown),
                     held_ids=frozenset(kind.id_of(record) for record in records))
    return shown
