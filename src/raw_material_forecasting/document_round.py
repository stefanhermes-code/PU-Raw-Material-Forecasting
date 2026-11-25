"""Read every new report in the watched folders, unattended.

Stefan, 26 October 2026, setting out what this is for:

    1. There are 4 reports automatically made every week and stored in one of the Sharepoint
       folders.
    2. The application is supposed to grab these reports and process them into:
       1. Pricing datapoints, and/or
       2. Market Intelligence signals
    3. It should not need any intervention from my side, because the structure by report is fixed.

Until this module there was no such thing. `intelligence_watch` found the files and `app.py` was its
only caller, so every report that ever entered this application did so because somebody opened the
Import page and clicked. The watch was a to-do list, not a pipeline.

What a round does with each document, by the kind its folder declares:

  briefing   - every item to the register, and to the engine's event store
  condition  - the graded chain reading, and one event per chemical it names; where it states no
               classification it is read for its signals instead, so nothing waits for a person
  monitor    - developments AND the prices it quotes, to the series they belong to
  newsletter - developments only. No chain to grade and no prices to store: the APBA NewsBulletin
               is a news bulletin, and neither template detector recognises it, correctly.

Nothing is skipped for want of a person. Where a document cannot be read the round says so and
leaves it in the folder; the next round tries again, and the Import page still opens it by hand.

**The rights class is not guessed.** The engine refuses an import without one - Stefan, 5 October
2026: "no default, stated per document, and the engine refuses the import without it". For an
unattended round that has to be stated somewhere, so it is stated once per report family in
`intelligence_folders.json`. A folder with no `rights_class` has its prices left for the page,
because the alternative is this module inventing an answer to a question he reserved for himself.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from raw_material_forecasting import document_intake
from raw_material_forecasting import intelligence_bridge as bridge
from raw_material_forecasting import intelligence_register as recorded
from raw_material_forecasting import intelligence_watch as watch
from raw_material_forecasting import supply_conditions
from raw_material_forecasting import units

# Enough mentions to be what a report is about rather than an aside. Matches `_event_of` on the
# page, and for the same reason: the file name is not evidence of what is inside it.
HORMUZ_MENTIONS = 2


@dataclass
class Outcome:
    """What one round did, in the words a run log should carry."""

    read: int = 0
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    developments: int = 0
    conditions: int = 0
    prices: int = 0
    events: int = 0
    held_back: list[tuple[str, str]] = field(default_factory=list)
    # Reports that state no classification. They are not held back - they are read for signals
    # instead - but the chain has no reading for that day and the run should say so.
    ungraded: list[tuple[str, int]] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{self.read} documents read: {self.developments} developments, "
               f"{self.conditions} chain readings, {self.prices} prices, {self.events} events."]
        if self.ungraded:
            out.append(f"  {len(self.ungraded)} stated no classification and were read for signals "
                       f"instead; the chain has no reading for those days.")
        for name, why in self.held_back:
            out.append(f"  held back: {name} - {why}")
        for name, why in self.unreadable:
            out.append(f"  could not read: {name} - {why}")
        return out


def event_of(name: str, text: str) -> str:
    """What a report is about, read from the report rather than from its file name.

    Stefan, 18 October 2026: "You are judging by report title....not by content.....that is
    dangerous." The April to June updates are named "PU_Supply_Chain_Geopolitical_Update_..." and
    are about the Strait of Hormuz, three to seven mentions each.
    """
    if str(text or "").lower().count("hormuz") >= HORMUZ_MENTIONS:
        return "Strait of Hormuz disruption"
    return "Strait of Hormuz disruption" if "hormuz" in str(name or "").lower() else ""


def _price_rows(document, file_name: str, document_reader, price_import) -> list[dict[str, Any]]:
    """Every complete price the reader proposes, as the engine's rows.

    A row the reader could not complete is left out and counted, not forced: an incomplete price is
    a price with no market to sit in.
    """
    if document_reader is None or not document_reader.configured():
        return []
    try:
        proposed = document_reader.read_rows(document)
    except Exception:
        return []
    rows = []
    for row in proposed:
        if not row.ready:
            continue
        values = dict(row.values)
        values.update({
            "product": values.get("chemical", ""),
            "market_geography": values.get("geography", ""),
            "commercial_basis": values.get("basis", ""),
        })
        values.setdefault("unit", units.MT)
        values.setdefault("packaging", "Not specified")
        rows.append(values)
    return rows


def run(*, data_dir: Path, folders: Sequence[Any] | None = None,
        send_events: Callable[[str, list[dict]], dict] | None = None,
        send_prices: Callable[[dict], dict] | None = None,
        document_reader=None, price_import=None, today: date | None = None) -> Outcome:
    """One pass over the watched folders. Everything new is read and stored.

    `send_events` and `send_prices` are passed in rather than reached for, so a round can be run
    against a test engine, and so a round with no engine still writes the register and the chain -
    the page has to keep working when the engine is down.
    """
    outcome = Outcome()
    folders = list(folders if folders is not None else watch.folders())
    held = watch.held_documents(recorded.load(data_dir), supply_conditions.load(data_dir))
    arrivals = watch.unread(folders, data_dir=data_dir, held=held)
    rights_of = {folder.name: str(getattr(folder, "rights_class", "") or "") for folder in folders}

    new_developments: list[Any] = []
    new_conditions: list[Any] = []

    for arrival in sorted(arrivals, key=lambda a: str(a.written)):
        try:
            document = document_intake.read(arrival.name, arrival.file.read_bytes())
        except Exception as problem:
            outcome.unreadable.append((arrival.name, f"{type(problem).__name__}: {problem}"))
            continue
        outcome.read += 1
        kind = arrival.kind if arrival.kind != "any" else _detected(document)

        if kind in ("briefing", "monitor", "newsletter"):
            # A newsletter has no template to read by, so it goes to the reader that takes
            # developments out of prose - the same one a pricing report's commentary goes to.
            new_developments += _developments(
                document, arrival, "monitor" if kind == "newsletter" else kind, today)

        if kind == "condition":
            found = supply_conditions.read_report(
                document, name=arrival.name, event=event_of(arrival.name, document.text))
            if found.state:
                new_conditions.append(found)
            # The reading is the state, and a state is never guessed from prose here -
            # "remains below a full critical stop" is not a reading of Critical. But a report is
            # read for its signals whether it grades the chain or not, so nothing waits for a
            # person. Stefan, 26 October 2026: "It should not need any intervention from my side."
            #
            # Until 25 November 2026 only an UNGRADED report was read for them. Every special
            # report since 30 October states a grade, so the grade on the page moved each week and
            # the signals under it stopped: the Logistics line was still quoting the sinkings of
            # 13 October on 25 November, with three later reports unread for what they said.
            signals = _developments(document, arrival, "monitor", today)
            new_developments += signals
            if not found.state:
                outcome.ungraded.append((arrival.name, len(signals)))

        if kind == "monitor" and send_prices is not None:
            rights = rights_of.get(arrival.folder, "")
            if not rights:
                outcome.held_back.append((arrival.name, "its folder states no rights class, so its "
                                                        "prices wait for the Import page"))
            else:
                rows = _price_rows(document, arrival.name, document_reader, price_import)
                if rows:
                    outcome.prices += _send_prices_by_issuer(
                        send_prices, arrival, rows, rights)

    if new_developments:
        added, _total = recorded.append(new_developments, data_dir)
        outcome.developments = added
    if new_conditions:
        added, _total = supply_conditions.append(new_conditions, data_dir)
        outcome.conditions = added

    # What was read is remembered by content, so the waiting list does not offer it again whatever
    # the file is called next time.
    if new_developments or new_conditions:
        from raw_material_forecasting import document_identity as _identity

        store = _identity.load(data_dir)
        for arrival in arrivals:
            got = _identity.identity_of(
                arrival.file, store, read_day=supply_conditions.day_of_document,
                open_document=document_intake.read)
            _identity.record(got, arrival.folder, store)
        _identity.save(store, data_dir)

    if send_events is not None:
        outcome.events += _send(send_events, bridge.BRIEFING,
                                _development_events(new_developments))
        outcome.events += _send(send_events, bridge.CONDITION,
                                bridge.conditions_to_events(new_conditions))
    return outcome


def _detected(document) -> str:
    """The kind of a document dropped into the by-hand folder, from what is in it."""
    from raw_material_forecasting import briefing_reader, monitor_reader

    text = str(getattr(document, "text", "") or "")
    if briefing_reader.looks_like_a_briefing(text):
        return "briefing"
    if "special report" in text[:6000].lower() and "supply chain" in text[:6000].lower():
        return "condition"
    if monitor_reader.looks_like_a_monitor(text):
        return "monitor"
    return "briefing"


def _developments(document, arrival, kind: str, today: date | None = None) -> list[Any]:
    """Every item a briefing or a pricing report reports, as register rows.

    Nothing is dropped for naming no chemical or no market: those items are the Hormuz and
    corporate ones, and the gates that used to drop them came out on 21 October 2026.
    """
    from raw_material_forecasting import briefing_reader, monitor_reader

    rows = []
    if kind == "briefing":
        briefing = briefing_reader.read(document.text, name=arrival.name)
        for item in briefing.items:
            named = recorded.names_a_source(item.headline, item.context, item.implication)
            rows.append(recorded.from_briefing(
                item, document=arrival.name,
                reported=briefing.covers_to or briefing.prepared,
                attribution=", ".join(named)))
        return rows
    try:
        found = monitor_reader.read_developments(document)
    except Exception:
        return []
    # The day the report was written, for an item that names no day of its own. Never today's
    # date: a development read out of last week's report happened last week, and `available_from`
    # is what a forecast's cutoff is applied to.
    # The day the report states, and nothing else. An item that names no day of its own is dated
    # by the report that carries it; a report that states no day yields no rows, because a row
    # filed on the day its file happened to be saved is a reading of a day that never happened.
    filed = supply_conditions.day_of_document(document)
    if not filed:
        return []
    # A monitor's proposal is not a briefing item - `from_briefing` reads fields it does not have -
    # so the row is built the way the page builds it on the one-at-a-time screen.
    for item in found:
        named = recorded.names_a_source(item.headline, item.context, item.implication)
        day = _day_of(item)
        rows.append(recorded.Entry(
            reported=day or filed,
            event_date=day,
            precision="DAY" if day else "PERIOD",
            headline=item.headline,
            kind=getattr(item, "kind", "") or "",
            chemicals=";".join(getattr(item, "chemicals", ()) or ()),
            geographies=";".join(getattr(item, "geographies", ()) or ()),
            confidence=getattr(item, "confidence", "") or "",
            context=getattr(item, "context", "") or "",
            implication=getattr(item, "implication", "") or "",
            quote=getattr(item, "quote", "") or "",
            attribution=", ".join(named),
            document=arrival.name,
            stored_on=(today or date.today()).isoformat()))
    return rows


def _day_of(item) -> str:
    """The day an item names, whether the reader handed back a date or the text of one.

    `briefing_reader` gives a `date`; `monitor_reader` gives a string. Both reach here.
    """
    day = getattr(item, "day", None)
    if day is None or day == "":
        return ""
    return day.isoformat() if hasattr(day, "isoformat") else str(day).strip()


def _development_events(rows: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for document, these in bridge.by_document(rows).items():
        out += bridge.to_events(these, document=document)
    return out


def _send(send_events, family: str, events: list[dict]) -> int:
    if not events:
        return 0
    try:
        return int((send_events(family, events) or {}).get("stored") or 0)
    except Exception:
        return 0


def _send_prices_by_issuer(send_prices, arrival, rows: list[dict], rights: str) -> int:
    """One import per issuer, as the page does: a document quoting three assessors is three."""
    by_issuer: dict[str, list[dict]] = {}
    for row in rows:
        by_issuer.setdefault(str(row.get("publisher") or "").strip() or "HTC Global", []).append(row)
    stored = 0
    content = base64.b64encode(arrival.file.read_bytes()).decode("ascii")
    for issuer, these in sorted(by_issuer.items()):
        try:
            answer = send_prices({"file_name": arrival.name, "content_base64": content,
                                  "publisher": issuer, "rights_class": rights, "rows": these})
            stored += int((answer or {}).get("stored") or 0)
        except Exception:
            continue
    return stored
