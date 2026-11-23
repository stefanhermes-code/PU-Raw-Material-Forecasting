"""Stefan's own hand on the customer pages: a Yes or No per time series, per page.

Stefan, 13 October 2026: "Of course this needs to be editable in the table ... how else am I going
to take control. Just this column is enough."

It was not enough. One column decided all three price pages together, and on 16 October Adipic Acid
Saudi Arabia needed to come off Forecast - its feed had stopped in April and the card was headed
"MAY 2026" - while staying on Historic Pricing, where its history is perfectly good. The single
control could only take it off everything or leave it everywhere. Stefan: "All 3 columns to be
editable."

So a choice is now recorded per page. Where Stefan sets a page's cell to something other than what
the evaluation says, his choice is kept here and wins on that page alone:

  No   the series is taken off that page. It stays in the store and still feeds the forecasts.
  Yes  the series is offered on that page even where the evaluation said No. Readings a ruling set
       aside are drawn again for it, and a price type the pages normally leave out (a producer
       posting, a seller quote) is let through for it.

Setting a cell back to what the evaluation says removes that page's choice, and the page follows the
evaluation again.

One exception, agreed with Stefan on 16 October: a Yes cannot put a market on the Forecast page when
its newest reading is older than the longest horizon. That bound is not a judgement about quality
that an editor should be able to outrank - it is arithmetic. Every horizon from a reading that old
lands in a month that has already happened, so there is no forecast to show, and `market_lines`
holds the rule rather than this module.

Readings are a property of the series and not of a page, so a Yes on ANY page restores what a ruling
set aside: a line a page is told to show has to exist before the page can show it.

One small JSON file next to the engine's data, so the application and the engine read the same
choices: %LOCALAPPDATA%/HTCGlobal/PUForecast/customer_page_controls.json, or the path in
PUFORECAST_CONTROLS. A choice written by the single-column version is read as the same answer on all
three pages, which is what it meant.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from raw_material_forecasting.market_basis import stated_packaging

FILE_NAME = "customer_page_controls.json"
_RECHECK_SECONDS = 2.0

# The three price pages a choice can be made for. Market Intelligence is a fact about a chemical and
# a geography rather than about a series, so it is not one of these.
CURRENT = "Current Market"
HISTORIC = "Historic Pricing"
FORECAST = "Forecast"
PAGES = (CURRENT, HISTORIC, FORECAST)

_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "checked": 0.0, "choices": {},
                          "shown": {}, "hidden": {}}
_EMPTY: dict[str, Any] = {"choices": {}, "shown": {}, "hidden": {}}
_local = threading.local()


def path() -> Path:
    stated = os.environ.get("PUFORECAST_CONTROLS", "").strip()
    if stated:
        return Path(stated)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "HTCGlobal" / "PUForecast" / FILE_NAME
    return Path.home() / ".htcglobal" / "puforecast" / FILE_NAME


def _text(value: Any) -> str:
    return str(value or "").strip()


def key(chemical: str, geography: str, terms: str, packaging: str) -> str:
    return "|".join((_text(chemical), _text(geography), _text(terms).upper(), _text(packaging)))


def _pages_of(entry: dict[str, Any]) -> dict[str, bool]:
    """The per-page answers in one stored choice.

    A choice written by the single-column version carries `show` and no `pages`. It meant every
    price page, so that is how it is read - no migration pass, no file rewritten behind Stefan's
    back, and a file written by either version works in both.
    """
    stated = entry.get("pages")
    if isinstance(stated, dict):
        return {page: bool(stated[page]) for page in PAGES if page in stated}
    if "show" in entry:
        return {page: bool(entry["show"]) for page in PAGES}
    return {}


def baseline_of(entry: dict[str, Any]) -> dict[str, bool]:
    """What the evaluation said for each page at the moment the choice was made.

    The review mark needs this. Without it the only question that can be asked is "does Stefan
    disagree with the evaluation", which is permanently true for a standing judgement - he set
    several regional assessments to No for having fewer than ten readings, a reason the evaluation
    does not weigh and never will, so those cells would carry a mark for ever and the mark would
    mean nothing (Stefan, 16 October 2026, on a four-reading row: "makes me wonder how the icons
    are placed").

    What the mark should say is that something MOVED since the choice. Absent for a choice written
    before 16 October; those stay unmarked until the next time they are set, which is honest - we
    do not know what the evaluation said back then and will not guess.
    """
    stated = entry.get("evaluated")
    if not isinstance(stated, dict):
        return {}
    return {page: bool(stated[page]) for page in PAGES if page in stated}


def _read(where: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    choices = payload.get("choices") if isinstance(payload, dict) else None
    return {str(k): v for k, v in (choices or {}).items()
            if isinstance(v, dict) and _pages_of(v)}


def _state() -> dict[str, Any]:
    if getattr(_local, "suspended", False):
        return _EMPTY
    where = path()
    now = time.monotonic()
    with _lock:
        if _cache["path"] == where and now - _cache["checked"] < _RECHECK_SECONDS:
            return _cache
        try:
            mtime = where.stat().st_mtime
        except OSError:
            mtime = None
        if _cache["path"] != where or mtime != _cache["mtime"]:
            read = _read(where) if mtime is not None else {}
            _cache["choices"] = read
            shown: dict[str, set] = {page: set() for page in PAGES}
            hidden: dict[str, set] = {page: set() for page in PAGES}
            for series_key, entry in read.items():
                if len(series_key.split("|")) != 4:
                    continue
                for page, answer in _pages_of(entry).items():
                    (shown if answer else hidden)[page].add(_split(series_key))
            _cache["shown"] = {page: frozenset(keys) for page, keys in shown.items()}
            _cache["hidden"] = {page: frozenset(keys) for page, keys in hidden.items()}
            _cache["mtime"] = mtime
            _cache["path"] = where
        _cache["checked"] = now
        return _cache


def _split(series_key: str) -> tuple[str, str, str, str]:
    a, b, c, d = series_key.split("|")
    return (a, b, c, d)


def choices() -> dict[str, dict[str, Any]]:
    """Every choice Stefan has made, re-read when the file changes (checked every two seconds)."""
    return _state()["choices"]


def _across(side: str) -> frozenset:
    """Every series answered that way on any page."""
    per_page = _state()[side]
    return frozenset().union(*per_page.values()) if per_page else frozenset()


def hidden(page: str | None = None) -> frozenset:
    """(chemical, geography, TERMS, packaging) Stefan has taken off `page`.

    With no page, those he has taken off EVERY price page - the only reading of "hidden" that still
    means hidden, now that a series can be off one page and on another.
    """
    if page is None:
        per_page = _state()["hidden"]
        return (frozenset.intersection(*per_page.values()) if len(per_page) == len(PAGES)
                else frozenset())
    return _state()["hidden"].get(page, frozenset())


def shown(page: str | None = None) -> frozenset:
    """(chemical, geography, TERMS, packaging) Stefan has put on `page` by hand.

    With no page, those he has put on ANY page. Curation asks it that way: a reading set aside by a
    ruling has to be drawn again before a page it was told to appear on can show a line at all.
    """
    if page is None:
        return _across("shown")
    return _state()["shown"].get(page, frozenset())


# The same as market_lines, so a key here names the same series as a selection there.
# One copy, in market_basis, beside the other fields of the key.


def stated(packaging: Any) -> str:
    """The name this module's callers use. One implementation, in market_basis."""
    return stated_packaging(packaging)


def set_choice(series_key: str, show: bool | None, *, page: str | None = None,
               reason: str = "", evaluated: bool | None = None) -> None:
    """Record a choice for one page, or remove it with None. Written whole, then swapped in.

    `page` None sets the same answer on all three, which is what the single column used to do and
    what a caller that does not care about pages still means.

    `evaluated` is what the evaluation says for that page right now, kept beside the choice so the
    table can later tell a choice that has gone stale from one that simply differs. See
    `baseline_of`.
    """
    pages = PAGES if page is None else (page,)
    where = path()
    current = _read(where)
    entry = dict(current.get(series_key) or {})
    answers = _pages_of(entry)
    entry.pop("show", None)              # superseded by `pages`; never written again
    baseline = baseline_of(entry)
    for one in pages:
        if show is None:
            answers.pop(one, None)
            baseline.pop(one, None)
        else:
            answers[one] = bool(show)
            if evaluated is not None:
                baseline[one] = bool(evaluated)
    if not answers:
        current.pop(series_key, None)
    else:
        entry["pages"] = answers
        if baseline:
            entry["evaluated"] = baseline
        else:
            entry.pop("evaluated", None)
        entry["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if reason:
            entry["reason"] = reason
        current[series_key] = entry
    where.parent.mkdir(parents=True, exist_ok=True)
    temporary = where.with_suffix(".tmp")
    temporary.write_text(json.dumps({"choices": current}, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    os.replace(temporary, where)
    with _lock:
        _cache["path"] = None   # read again on the next call


class suspended:
    """The evaluation on its own, without Stefan's choices, so the table can show both."""

    def __enter__(self):
        self._before = getattr(_local, "suspended", False)
        _local.suspended = True
        return self

    def __exit__(self, *exc):
        _local.suspended = self._before
        return False
