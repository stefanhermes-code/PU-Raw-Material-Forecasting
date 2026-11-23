"""One honest status line per market-data source, derived from what the engine recorded.

The engine's ingestion state alone is misleading: a fetch that returned a page with no
recognisable price is logged as SUCCESS. Here "success" means *data on hand*.

Whether a source has stopped delivering is measured against its own rhythm, which is
`collection_watch`'s rule and not a second one: the cadence is the median gap between this source's
own recent reading days, and lateness is a multiple of that. Until 31 October 2026 it was fourteen
days flat for every publisher, which is the mistake Stefan named as standing: "not assume all is
daily to make something declare it is missing, that is a basic mistake you make all the time." On
the live store it called 51 of 93 sources stale - 36 of them monthly series doing exactly what a
monthly series does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from .collection_watch import CURRENT, cadence_of, rhythm_name, state_of
from .market_basis import BASIS_LABEL, display_basis
from .trade_feeds import arrival_days

# Everything below is an *interval* - how often a publisher prints - and never a window. The window
# is `collection_watch.state_of`'s, and nothing here second-guesses it: late past twice the
# interval, quiet past four times, and nothing called late inside five days whatever it says.
#
# How often each declared cadence prints, in days. A compound name takes its slowest leg, because a
# feed described DAILY_TO_MONTHLY has missed nothing until a month has gone by. Read only when a
# source sends no reading days; one that does is judged on what it actually did.
PUBLICATION_INTERVAL_DAYS = {
    "DAILY": 1, "INTRADAY_DAILY_WEEKLY": 7, "DAILY_WEEKLY": 7, "DAILY_TO_MONTHLY": 31,
    "WEEKLY": 7, "WEEKLY_REALTIME_NEWS": 7, "WEEKLY_MONTHLY_QUARTERLY": 92,
    "MONTHLY": 31, "MONTHLY_EVENT_DRIVEN": 31,
    "QUARTERLY": 92, "REALTIME_QUARTERLY_NEWS": 92,
    "ANNUAL": 365,
}
# A publisher that prints when something happens has no interval to miss, so there is nothing to be
# late against. Six weeks here means nothing is said until a quarter has gone by, which is as long
# as a thing with no schedule can reasonably be left unexamined.
NO_FIXED_CADENCE = frozenset({"EVENT_DRIVEN", "IRREGULAR", "HISTORICAL"})
NO_CADENCE_INTERVAL_DAYS = 45
# A cadence nobody has recorded - seven rows on the live store carry none at all. Weekly, which is
# the fourteen-day window this module applied to every source until 31 October 2026, so a source
# with no recorded cadence behaves exactly as it always did instead of quietly passing.
UNRECORDED_INTERVAL_DAYS = 7

# A source with one of these is not fetched whatever its refresh frequency says: nothing in the
# code registry collects it.
NOT_COLLECTED: frozenset[str] = frozenset({"MONITOR_ONLY", "MANUAL_IMPORT"})


def we_fetch(source: dict[str, Any]) -> bool:
    """Whether we go and get this one ourselves. Everything else is held for the record.

    Four tests, and each has been the only one to catch a case. The refresh frequency is what the
    registry says about calling it; the collector status is what the code says about having a
    collector at all, and a row that lives only in the database carries one from v1.2.9;
    `internal://` is a lane of our own.

    The fourth is the plainest and was missing until 5 November 2026: a source that states an empty
    address is not fetched. `europe_published_ddp` holds an empty `base_url` and collects by
    REFERENCE_SERIES - it is the store's own reference series republished as a DDP market, and
    nothing has ever gone to get it - but it says MONTHLY, so the first test passed it, and an
    empty string does not start with `internal://`, so the third passed it too. It was judged as a
    feed and reported four months silent, with no ingestion run in its history to be silent in. It
    is the only such row today; the test states the rule rather than the name.

    An ABSENT `base_url` is not an empty one, and the difference is the whole care in this test. A
    caller that never mentions the field has said nothing about the address, and reading silence as
    "no address" is this application's oldest recurring defect - `dict.get` returns None and nobody
    fails. Written the strict way for an hour, it relabelled every collected reading in
    `market_lines` as history, because the readings there are judged against source dicts that
    carry an id and a frequency and nothing else.

    This was `_is_fetched` in `app.py` and nowhere else, so the only thing that could ask "did we
    collect this reading?" was the application - and `market_lines` answered it with a date instead
    (masterplan G). One judge, here, read by both sides.
    """
    return (str(source.get("refresh_frequency") or "").upper() not in {"MANUAL", "NONE"}
            and str(source.get("collector_status") or "") not in NOT_COLLECTED
            and ("base_url" not in source or bool(str(source.get("base_url") or "").strip()))
            and not str(source.get("base_url") or "").startswith("internal://"))


def fetched_ids(sources: Any) -> frozenset[str]:
    """The ids of the sources we fetch, from a snapshot's source list."""
    return frozenset(str(one.get("source_id") or "") for one in (sources or ())
                     if we_fetch(one))


@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    label: str          # LIVE DATA | COMPLETE | STALE DATA | NO DATA | FAILED | MONITOR ONLY | MANUAL IMPORT | NOT CHECKED
    detail: str
    rank: int           # for ordering: problems first? no - data first, then problems, then monitor-only
    delivering: bool


# A source that covers a period which has closed and delivered it. `comtrade_monthly_2016` holds
# 974 readings ending 1 December 2016 and will never print again, because 2016 will not happen
# again. Judged on how old its newest reading is, it is broken every day for ever; it is finished.
COMPLETE = "COMPLETE"


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _short(text: Any, limit: int = 110) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _days(values: Any) -> list[date]:
    out = [day for value in (values or ()) if (day := _date(value)) is not None]
    return sorted(set(out))


def declared_interval_days(source: dict[str, Any]) -> int:
    """How often this source says it prints, for when its reading days cannot be measured."""
    cadence = str(source.get("expected_publish_frequency") or "").upper()
    if cadence in NO_FIXED_CADENCE:
        return NO_CADENCE_INTERVAL_DAYS
    return PUBLICATION_INTERVAL_DAYS.get(cadence, UNRECORDED_INTERVAL_DAYS)


def _covering_period_closed(source: dict[str, Any], *, today: date) -> date | None:
    """The last day this source covers, if that day has passed. `None` for an open feed."""
    closes = _date(source.get("covers_until"))
    return closes if closes is not None and closes < today else None


def classify_source(source: dict[str, Any], *, today: date) -> SourceStatus:
    source_id = str(source.get("source_id") or "")
    count = int(source.get("observation_count") or 0)
    latest = _date(source.get("latest_observation_date"))
    state = str(source.get("last_attempt_state") or "")
    error = str(source.get("last_attempt_error") or "")
    collector = str(source.get("collector_status") or "")
    notes = str(source.get("notes") or "")

    # Held, not collected. `we_fetch` is the one judge of that and it reads four fields; this
    # asked a narrower question of its own until 31 October 2026 - a MANUAL frequency or an
    # `internal://` URL - so the seven `import_*` sources, which say NONE and MANUAL_IMPORT, were
    # judged as feeds and reported late when nobody had imported into them for a fortnight.
    if not we_fetch(source):
        # A page we watch and take nothing from says why, in the registry's own words. One of these
        # does hold readings - `europe_published_ddp`, a reference series derived from the store -
        # and readings it was given are an import however they arrived.
        if collector == "MONITOR_ONLY" and not count:
            return SourceStatus(source_id, "MONITOR ONLY",
                                _short(notes.split(". ")[0] if notes else "no public price on this page"),
                                5, False)
        if count:
            return SourceStatus(source_id, "MANUAL IMPORT", f"{count} imported observations, latest {latest}", 3, True)
        return SourceStatus(source_id, "MANUAL IMPORT", "no observations imported", 6, False)

    if count and latest is not None:
        closed = _covering_period_closed(source, today=today)
        # A closed window is judged against the end of the period it covers, not against today.
        # `comtrade_annual_2000_2008` printing 2008 in 2026 has done its job; the same source
        # stopping at 2015 when its block runs to 2017 has not, and that difference is the point.
        as_of = closed or today
        # The rhythm this source actually kept, where the engine sent the days it printed on;
        # otherwise the one it says it keeps. One window either way - `state_of` decides - so the
        # two cannot drift into disagreeing about the same source, which they did for an afternoon
        # while this was written: measured allowed two intervals, declared allowed one and a grace.
        printed = _days(arrival_days(source))
        if printed:
            newest, cadence = printed[-1], cadence_of(printed)
            how = f"and it prints {rhythm_name(cadence)}"
        else:
            newest, cadence = latest, declared_interval_days(source)
            how = f"and it is recorded as printing {rhythm_name(cadence)}"
        since = max(0, (as_of - newest).days)
        # Not `state`: that one holds how the last fetch ended, and is read below this branch.
        standing = state_of(since, cadence)
        if standing == CURRENT:
            if closed is not None:
                return SourceStatus(source_id, COMPLETE,
                                    f"{count} observations, complete to {closed}", 3, True)
            return SourceStatus(source_id, "LIVE DATA", f"{count} observations, latest {latest}", 1, True)
        if closed is not None:
            return SourceStatus(source_id, "STALE DATA",
                                f"{count} observations ending {newest}, but its period runs to {closed}",
                                2, True)
        return SourceStatus(source_id, "STALE DATA",
                            f"{count} observations, latest {latest} - {standing}, "
                            f"nothing for {since} days {how}", 2, True)

    if state == "FAILED":
        return SourceStatus(source_id, "FAILED", _short(error or "last attempt failed"), 4, False)
    if state:
        return SourceStatus(source_id, "NO DATA", _short(error or "page fetched, no price recognised"), 4, False)
    return SourceStatus(source_id, "NOT CHECKED", "no collection attempt recorded yet", 6, False)


# --------------------------------------------------------- is our collection the reason it stopped?
#
# Stefan, 5 November 2026, told that nineteen markets had been dark for four to nine months in
# three batches - ten South Korean on one day, nine Saudi on another: "Yes, find out why."
#
# They are all UN Comtrade customs figures, and our collection was never the reason. The connector
# ran weekly and succeeded every time: `comtrade_monthly_2026` SUCCESS on 13, 21 and 28 October,
# South Korea (410) and Türkiye (792) named in every request. Asked directly on 5 November 2026,
# Comtrade's own API returns nothing for South Korea, Türkiye or China for any month of 2026, and
# Saudi Arabia stops at April - exactly what the store holds. The countries have not filed.
#
# Measured over the 39 markets then on the quiet list: 37 had every live feed collected
# successfully AFTER the market's last reading, and 2 are fed by hand-loaded imports with no feed
# at all. None was a collection failure.
#
# So this answers the question the page could not: when a market goes quiet, did WE stop asking?
# It names a fault and says nothing when there is none, because a column that reads the same on 37
# of 39 rows is the defect v1.2.62 removed, not a column.
#
# A closed archive block is left out on purpose. A market's readings come from a dozen Comtrade
# sources, eleven of them finished years - `comtrade_monthly_2016` will not print again, and
# counting those gave "some feeds stale, some healthy" on 31 of 39 rows, which told a reader
# nothing. Only a feed that still has something to print can be at fault.
#
# STALE DATA is left out too, and that is the whole point: a stale feed IS the market being quiet.
# Reporting it here would answer the question with the question.
COLLECTING = "-"
NO_LIVE_FEED = "nothing fetches this - imports only"
AT_FAULT = {"FAILED": "fetch failing", "NO DATA": "fetched, no price found"}


def feeds_by_pair(observations: Any) -> dict[tuple[str, str], set[str]]:
    """Which sources produced each chemical-in-a-geography's readings.

    Keyed on `populations.PAIRS` - the same two fields `collection_watch.watch` groups on - so a
    row of that watch can be matched to the feeds behind it without a second idea of what a
    market is.
    """
    out: dict[tuple[str, str], set[str]] = {}
    for row in observations or ():
        chemical = str(row.get("product_family") or "").strip()
        geography = str(row.get("market_geography") or "").strip()
        source = str(row.get("source_id") or "")
        if not chemical or not geography or not source:
            continue
        out.setdefault((chemical, geography), set()).add(source)
    return out


def live_feeds(feeds: Any, sources: Any, *, today: date) -> list[dict[str, Any]]:
    """The feeds behind a market that still have something to print: fetched, and not finished."""
    rows = {str(one.get("source_id") or ""): one for one in (sources or ())}
    out = []
    for source_id in sorted(feeds or ()):
        source = rows.get(source_id)
        if source is None or not we_fetch(source):
            continue
        if classify_source(source, today=today).label == COMPLETE:
            continue
        out.append(source)
    return out


def collection_fault(feeds: Any, sources: Any, *, since: date, today: date) -> str:
    """Why a quiet market is quiet, as far as OUR collection can be blamed for it.

    `since` is the day the market's newest reading became available - the same clock
    `last_successful_collection` is on, so "did we ask after it went quiet?" is a comparison and
    not a judgement. `COLLECTING` means we asked and nothing came back, which is the publisher's
    silence and not ours.
    """
    live = live_feeds(feeds, sources, today=today)
    if not live:
        return NO_LIVE_FEED
    broken = [(str(one.get("source_id") or ""), classify_source(one, today=today).label)
              for one in live]
    broken = [(source_id, label) for source_id, label in broken if label in AT_FAULT]
    if broken:
        return "; ".join(f"{AT_FAULT[label]}: {source_id}" for source_id, label in broken)
    asked = [day for one in live if (day := _date(one.get("last_successful_collection")))]
    if not asked or max(asked) <= since:
        return "not collected since"
    return COLLECTING


def next_check_text(source: dict[str, Any]) -> str:
    if bool(source.get("is_due")):
        return "due now"
    cadence = str(source.get("refresh_frequency") or "").upper()
    if cadence == "MANUAL":
        return "manual"
    last = source.get("last_attempt_at") or source.get("last_successful_collection")
    stamp = str(last).replace("T", " ")[:16] if last else None
    return f"tomorrow (checked {stamp})" if stamp else "next start"


def summarize(sources: list[dict[str, Any]], *, today: date) -> dict[str, int]:
    statuses = [classify_source(s, today=today) for s in sources]
    # Neither a hand-loaded import nor a closed archive is a feed that should be running, so
    # neither belongs in "76 of 93 delivering". Both hold data; nothing collects them.
    live = [s for s in statuses if s.label not in {"MANUAL IMPORT", COMPLETE}]
    return {
        "live_sources": len(live),
        "delivering": sum(1 for s in live if s.delivering),
        "monitor_only": sum(1 for s in live if s.label == "MONITOR ONLY"),
        "problems": sum(1 for s in live if s.label in {"FAILED", "NO DATA", "STALE DATA"}),
    }


# ----------------------------------------------------------------------------- coverage grid
CURRENT_WINDOW_DAYS = 14


def _identity_of(obs: dict[str, Any]) -> tuple[str, str, str]:
    geo = str(obs.get("market_geography") or "UNKNOWN")
    basis = str(obs.get("commercial_basis") or "UNKNOWN")
    packaging = str(obs.get("packaging") or "UNKNOWN").upper()
    if packaging in {"", "UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"}:
        packaging = "UNSPECIFIED"
    return geo, basis, packaging


def coverage_matrix(observations: list[dict[str, Any]], *, today: date, window_days: int = CURRENT_WINDOW_DAYS,
                    exclude_sources: set[str] | None = None) -> dict[str, Any]:
    """Sources x chemicals -> number of *current* price identities (geography x Incoterm x packaging).

    Returns {"chemicals": [...], "sources": [...], "cells": {(chemical, source): {"count", "latest", "identities"}},
             "row_totals": {chemical: n}, "col_totals": {source: n}, "total": n}
    """
    exclude_sources = exclude_sources or set()
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for obs in observations:
        source = str(obs.get("source_id") or "")
        chemical = str(obs.get("product_family") or "UNKNOWN").upper()
        if not source or source in exclude_sources:
            continue
        observed = _date(obs.get("observation_date"))
        if observed is None or (today - observed).days > window_days:
            continue
        if str(obs.get("observation_type") or "market_price") != "market_price":
            continue
        cell = cells.setdefault((chemical, source), {"identities": set(), "latest": observed})
        cell["identities"].add(_identity_of(obs))
        if observed > cell["latest"]:
            cell["latest"] = observed
    chemicals = sorted({c for c, _ in cells})
    sources = sorted({s for _, s in cells})
    for cell in cells.values():
        cell["count"] = len(cell["identities"])
    row_totals = {c: sum(cell["count"] for (cc, _), cell in cells.items() if cc == c) for c in chemicals}
    col_totals = {s: sum(cell["count"] for (_, ss), cell in cells.items() if ss == s) for s in sources}
    return {
        "chemicals": chemicals, "sources": sources, "cells": cells,
        "row_totals": row_totals, "col_totals": col_totals, "total": sum(row_totals.values()),
        "window_days": window_days,
    }


def coverage_table(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for display: one per chemical, one column per source, plus a total column."""
    rows = []
    for chemical in matrix["chemicals"]:
        row: dict[str, Any] = {"Chemical": chemical}
        for source in matrix["sources"]:
            cell = matrix["cells"].get((chemical, source))
            row[source] = cell["count"] if cell else 0
        row["Current numbers"] = matrix["row_totals"][chemical]
        rows.append(row)
    if rows:
        total: dict[str, Any] = {"Chemical": "All"}
        for source in matrix["sources"]:
            total[source] = matrix["col_totals"][source]
        total["Current numbers"] = matrix["total"]
        rows.append(total)
    return rows


def coverage_detail(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Every current number, one line each: chemical, source, geography, basis, packaging, date."""
    out = []
    for (chemical, source), cell in sorted(matrix["cells"].items()):
        for geo, basis, packaging in sorted(cell["identities"]):
            out.append({"Chemical": chemical, "Source": source, "Geography": geo,
                        BASIS_LABEL: display_basis(basis),
                        "Packaging": packaging, "Latest": cell["latest"].isoformat()})
    return out


# ----------------------------------------------------------------------------- price matrix
# A publisher is only judged by its own cadence: a monthly assessment is not "stale" after two weeks.
CADENCE_WINDOW_DAYS = {"DAILY": CURRENT_WINDOW_DAYS, "DAILY_WEEKLY": CURRENT_WINDOW_DAYS,
                       "WEEKLY": 21, "MONTHLY": 45, "QUARTERLY": 120,
                       "REALTIME_QUARTERLY_NEWS": 120, "INTRADAY_DAILY_WEEKLY": CURRENT_WINDOW_DAYS}


def window_by_source(sources: list[dict[str, Any]], *, default: int = CURRENT_WINDOW_DAYS) -> dict[str, int]:
    """Freshness window per source, from its expected publication frequency."""
    out: dict[str, int] = {}
    for source in sources:
        cadence = str(source.get("expected_publish_frequency") or "").upper()
        out[str(source.get("source_id"))] = CADENCE_WINDOW_DAYS.get(cadence, default)
    return out


def price_matrix(observations: list[dict[str, Any]], *, today: date, window_days: int = CURRENT_WINDOW_DAYS,
                 product_family: str | None = None, exclude_sources: set[str] | None = None,
                 windows: dict[str, int] | None = None) -> dict[str, Any]:
    """Latest current price per (geography, Incoterm, packaging) row and source column.

    Returns {"rows": [(geo, basis, packaging), ...], "sources": [...],
             "cells": {(row, source): {"price", "currency", "date", "source_price", "source_currency"}}}
    """
    exclude_sources = exclude_sources or set()
    cells: dict[tuple[tuple[str, str, str], str], dict[str, Any]] = {}
    origins: dict[tuple[str, str, str], str | None] = {}
    for obs in observations:
        source = str(obs.get("source_id") or "")
        if not source or source in exclude_sources:
            continue
        if product_family and str(obs.get("product_family") or "").upper() != product_family.upper():
            continue
        if str(obs.get("observation_type") or "market_price") != "market_price":
            continue
        observed = _date(obs.get("observation_date"))
        limit = (windows or {}).get(source, window_days)
        if observed is None or (today - observed).days > limit or obs.get("price") is None:
            continue
        row_key = _identity_of(obs)
        # Origin as the engine stored it, not recomputed here: the database is the governed record of
        # where the material was made, and the screen should show that rather than a second opinion.
        origins.setdefault(row_key, str(obs.get("origin") or "") or None)
        key = (row_key, source)
        current = cells.get(key)
        if current is None or observed > current["date"]:
            cells[key] = {
                "price": float(obs["price"]), "currency": str(obs.get("currency") or ""), "date": observed,
                "source_price": obs.get("source_price", obs.get("price")),
                "source_currency": obs.get("source_currency", obs.get("currency")),
            }
    rows = sorted({row for row, _ in cells}, key=lambda r: (r[0] != "China", r[0], r[1], r[2]))
    sources = sorted({s for _, s in cells})
    return {"rows": rows, "sources": sources, "cells": cells, "origins": origins,
            "window_days": window_days,
            "windows": {s: (windows or {}).get(s, window_days) for s in sources}}


NOT_ATTRIBUTED = "Not attributed"


def origin_label(origin: str | None) -> str:
    """What the reader should see. "UNKNOWN" is engine vocabulary; "Not attributed" is the fact."""
    value = str(origin or "").strip()
    if not value or value.upper() in {"UNKNOWN", "UNSPECIFIED", "NOT SPECIFIED", "NOT_SPECIFIED"}:
        return NOT_ATTRIBUTED
    return value


def price_matrix_table(matrix: dict[str, Any], *, source_label=lambda s: s, show_packaging: bool | None = None) -> list[dict[str, Any]]:
    """Display rows: Geography / (Origin) / Incoterm / (Packaging) / one column per source."""
    if show_packaging is None:
        show_packaging = any(row[2] != "UNSPECIFIED" for row in matrix["rows"])
    origins = matrix.get("origins") or {}
    # Two conditions, both required.
    #
    # The engine is the authority on origin - the application reaches it over HTTP and does not
    # import it - so if no observation carries an origin at all (an engine older than v0.4.70), the
    # column is hidden rather than filled with a guess. Saying "Not attributed" about a China EXW
    # price would be worse than saying nothing.
    #
    # And it only earns space when it says something Geography does not: for an origin-side Incoterm
    # the two are the same word, but for CFR and CIF they are emphatically not - which is exactly the
    # case a customer must not be left to guess at.
    has_origin_data = any(origins.get(row) for row in matrix["rows"])
    show_origin = has_origin_data and any(
        origin_label(origins.get(row)) != row[0] for row in matrix["rows"])
    out = []
    for row in matrix["rows"]:
        geo, basis, packaging = row
        line: dict[str, Any] = {"Geography": geo}
        if show_origin:
            line["Origin"] = origin_label(origins.get(row))
        line[BASIS_LABEL] = display_basis(basis)
        if show_packaging:
            line["Packaging"] = "Not specified" if packaging == "UNSPECIFIED" else packaging
        for source in matrix["sources"]:
            cell = matrix["cells"].get((row, source))
            line[source_label(source)] = f"{cell['price']:,.0f}  ({cell['date'].strftime('%d %b')})" if cell else "—"
        out.append(line)
    return out
