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
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

import pandas as pd
import streamlit as st

from .models import EntitlementScope, is_on, scope_to_dict, switched_off  # noqa: F401

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


# ----------------------------------------------------------------------------- shared small parts
def reason_fields(prefix: str) -> tuple[str, str]:
    actor = st.text_input("Changed by", value="HTC Platform Admin", key=f"{prefix}_actor")
    reason = st.text_input("Change reason", key=f"{prefix}_reason")
    return actor, reason


def safe_action(action):
    from .service import PlatformAdminError

    try:
        result = action()
        st.success("Saved")
        return result
    except (PlatformAdminError, ValueError) as exc:
        st.error(str(exc))
        return None


def pick_to_edit(label: str, items: Sequence[Any], *, id_of, name_of, key: str):
    """The record being edited, or nothing and a new one.

    These forms save with an upsert keyed on the id in the box, so a form that does not load what
    is already stored will replace it with blanks the moment an existing id is typed in. And with
    no chooser there was no other way to change a record at all: you had to know its id by heart
    and retype every field, and whatever you missed was silently cleared.

    It sits OUTSIDE the form on purpose. A selector inside `st.form` does nothing until Submit, so
    the fields could not be filled from it - the form would show the record chosen two clicks ago.
    The form's key carries the chosen id, so changing the choice rebuilds it.
    """
    if not items:
        return None
    by_id = {id_of(item): item for item in items}
    chosen = st.selectbox(label, ["", *by_id],
                          format_func=lambda i: "Create a new one" if not i else name_of(by_id[i]),
                          key=key)
    return by_id.get(chosen)


# ----------------------------------------------------------------------------- the three tabs
def _switch_off(kind: RecordKind, record: Any, *, deactivate, key: str) -> None:
    """Delete, as Stefan defined it: the record stops applying and the row stays."""
    now = datetime.now(timezone.utc)
    st.divider()
    if not is_on(record, at=now):
        st.caption(f"This {kind.noun} is already switched off. Edit it above to put it back.")
        return
    st.caption(
        f"Switching a {kind.noun} off keeps the row and everything that points at it. Nothing in "
        f"Platform Admin is deleted, so the change history stays readable.")
    with st.form(f"{key}_off_form"):
        actor, reason = reason_fields(f"{key}_off")
        if st.form_submit_button(f"Switch this {kind.noun} off", type="secondary"):
            safe_action(lambda: deactivate(record, actor=actor, reason=reason))


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


def _spreadsheet(kind: RecordKind, records: Sequence[Any], *, key: str) -> None:
    """Export what is held, edit it anywhere, bring it back.

    An import updates a row whose id already exists and creates one whose id does not - Stefan's
    choice, 31 October 2026 - so the sheet is a way of correcting twenty records at once and not
    only of loading the first twenty. Every row written goes through the same service call the form
    uses, so a spreadsheet cannot grant what a person could not.
    """
    held = pd.DataFrame([kind.to_row(record) for record in records])
    st.caption(
        f"Download what is held, change it in Excel, upload it back. A row whose "
        f"{kind.noun} ID already exists is updated; one whose ID is new is created.")
    if not held.empty:
        left, right = st.columns(2)
        left.download_button("Download as CSV", held.to_csv(index=False).encode("utf-8"),
                             file_name=f"{kind.noun}s.csv", mime="text/csv",
                             key=f"{key}_csv", width="stretch")
        right.download_button(
            "Download as Excel", _sheet_bytes(held), file_name=f"{kind.noun}s.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key}_xlsx", width="stretch")
    else:
        st.info(f"No {kind.noun} is held yet, so there is nothing to download. An upload still "
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

    known = {kind.id_of(record) for record in records}
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


def record_tabs(kind: RecordKind, records: Sequence[Any], draw_form, *, deactivate, key: str) -> None:
    """Create / Edit or Delete / CSV or Excel import, in that order, on every record page.

    `draw_form(None)` draws the empty form, `draw_form(record)` the same form filled in. It is the
    page's own, because the fields differ; the three tabs around it do not.

    `st.tabs` runs the body of every tab on every render, which is why the navigation above this
    does not use it. Here it costs nothing: the records were read once before these tabs, and the
    three panels only draw widgets over what is already in hand.
    """
    create_tab, edit_tab, sheet_tab = st.tabs(TAB_LABELS)
    with create_tab:
        draw_form(None)
    with edit_tab:
        if not records:
            st.info(f"No {kind.noun} is held yet. Create one first.")
        else:
            chosen = pick_to_edit(kind.noun.title(), records, id_of=kind.id_of,
                                  name_of=kind.name_of, key=f"{key}_pick")
            if chosen is None:
                st.caption(f"Choose a {kind.noun} above to edit or switch off.")
            else:
                draw_form(chosen)
                _switch_off(kind, chosen, deactivate=deactivate, key=key)
    with sheet_tab:
        _spreadsheet(kind, records, key=key)
