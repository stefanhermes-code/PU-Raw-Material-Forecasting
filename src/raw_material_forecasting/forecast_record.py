"""The forecast record, as a table a person can read and take away.

Stefan, 24 September 2026: "I think we should keep a history file for the forecast. Otherwise how
are we going to measure how good the forecasting method is/was?"

The engine writes every forecast down as it is served and appends the outcome once the month it
named has closed. This turns that store into the two things a reader wants from it: a count of how
much of the record has come due, and a row per claim showing what was said and what happened.

The rows come in two shapes, because the screen and the downloaded file want opposite things from a
claim that has not come due. `display_frame` is all text with a dash where nothing is known yet;
`csv_frame` keeps the numbers and leaves the cell empty. `display_rows` says why.

Kept free of Streamlit so the table can be built and checked without starting the application - the
same reason the banner's wording lives in `forecast_cards`.
"""
from __future__ import annotations

import pandas as pd

from .ui_helpers import UNKNOWN

COLUMNS = ("Made on", "For the month", "Method", "Months ahead", "Forecast", "Low", "High",
           "What happened", "Miss", "Inside the band", "State")


def _number(value):
    return None if value is None else round(float(value), 2)


def record_rows(record) -> list[dict]:
    """One row per recorded claim, newest first as the engine returns it.

    A claim that has not come due carries None in the outcome columns rather than a zero. Nought is
    a measurement and "not yet" is not, and a table that prints one for the other invites a reader
    to average them together.

    This is the measurement. The screen draws `display_rows`, which marks those Nones with a dash
    because Streamlit prints the word "None" for them; the download writes these, because a
    spreadsheet wants an empty cell and a real number.
    """
    rows = []
    for item in record or ():
        quantiles = item.get("quantiles") or {}
        rows.append({
            "Made on": item.get("as_of"),
            "For the month": str(item.get("period") or "")[:7],
            "Method": item.get("model"),
            "Months ahead": round(int(item.get("horizon_weeks") or 0) / 4),
            "Forecast": _number(item.get("value")),
            "Low": _number(quantiles.get("0.1")),
            "High": _number(quantiles.get("0.9")),
            "What happened": _number(item.get("outcome_value")),
            "Miss": _number(item.get("error")),
            "Inside the band": item.get("inside_band"),
            "State": item.get("state"),
        })
    return rows


#: The columns that carry a price, and so carry "not yet" until the claim comes due.
PRICE_COLUMNS = ("Forecast", "Low", "High", "What happened", "Miss")


def _price_text(value) -> str:
    """A price as a reader wants to see it, or the mark that says it is not known yet."""
    return UNKNOWN if value is None else f"{float(value):,.2f}"


def _plain(value) -> str:
    return str(value).strip() if str(value or "").strip() else UNKNOWN


def display_rows(record) -> list[dict]:
    """The record as a table of text, every unknown shown as the dash.

    Stefan, 1 November 2026, on making the empty cells read as a dash. The five price columns
    carried `None` for a claim that had not come due, the page coerced them to a numeric column,
    and Streamlit 1.63.0 with pandas 3.0.5 prints the word **None** in such a cell - measured in an
    isolated page on 31 October 2026, for a `float64` holding `NaN`, a nullable `Int64` holding
    `pd.NA` and a `string` holding `pd.NA` alike, with and without a `column_config`. Only a text
    column prints the dash. So most of this table read "None" five times across every row whose
    month had not closed yet, which looks like a fault in the page rather than "not yet".

    **What this costs, said plainly.** Streamlit sorts a text column alphabetically, so clicking
    "Miss" on screen now orders 100 before 20. The numbers themselves are not lost: the Download
    button below the table is built from `record_rows`, which is unchanged, so the CSV a buyer
    opens in a spreadsheet still holds real numbers and empty cells. The screen is for reading and
    the file is for sorting.

    **"Inside the band" is text here although it did not have to be.** The same measurement showed
    a `boolean` column holding `pd.NA` renders as an empty cell - no checkbox at all - so that one
    column was never printing "None". It is text anyway, because an unticked box and an empty cell
    are both "not a tick" at a glance, and the whole point of this change is that an unknown must
    not be mistaken for a finding. One mark across the table, so a reader learns it once.
    """
    rows = []
    for item in record or ():
        quantiles = item.get("quantiles") or {}
        inside = item.get("inside_band")
        rows.append({
            "Made on": _plain(item.get("as_of")),
            "For the month": _plain(str(item.get("period") or "")[:7]),
            "Method": _plain(item.get("model")),
            "Months ahead": str(round(int(item.get("horizon_weeks") or 0) / 4)),
            "Forecast": _price_text(_number(item.get("value"))),
            "Low": _price_text(_number(quantiles.get("0.1"))),
            "High": _price_text(_number(quantiles.get("0.9"))),
            "What happened": _price_text(_number(item.get("outcome_value"))),
            "Miss": _price_text(_number(item.get("error"))),
            "Inside the band": UNKNOWN if inside is None else ("yes" if inside else "no"),
            "State": _plain(item.get("state")),
        })
    return rows


def display_frame(record) -> pd.DataFrame:
    """The table the screen draws. All text, so nothing in it can render as "None"."""
    return pd.DataFrame(display_rows(record), columns=list(COLUMNS)).astype("string")


def csv_frame(record) -> pd.DataFrame:
    """The table the Download button writes: real numbers, and an empty cell for "not yet".

    Separate from `display_frame` on purpose. A spreadsheet wants a number it can total and a blank
    it can ignore; a dash in a price column would make the whole column text in Excel too, which is
    the cost the screen accepts and the file should not.
    """
    return pd.DataFrame(record_rows(record), columns=list(COLUMNS))


def record_counts(record) -> dict:
    """How much of the record is evidence yet.

    `inside` counts only the claims that carried a band. A forecast made without one did not get
    its band wrong, and counting it as a miss would understate every band that was offered.
    """
    items = list(record or ())
    settled = [item for item in items if item.get("outcome_value") is not None]
    banded = [item for item in settled if item.get("inside_band") is not None]
    return {
        "recorded": len(items),
        "settled": len(settled),
        "banded": len(banded),
        "inside": sum(1 for item in banded if item.get("inside_band")),
    }


def file_name(market: str) -> str:
    """A file a person can find again: forecast_record_TDI_China_EXW.csv."""
    import re

    stem = re.sub(r"[^A-Za-z0-9]+", "_", str(market or "market")).strip("_") or "market"
    return f"forecast_record_{stem}.csv"
