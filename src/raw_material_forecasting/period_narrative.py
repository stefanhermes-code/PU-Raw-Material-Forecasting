"""The sentences of the period report, composed from the figures and from nothing else.

Stefan, 8 October 2026: the report has to tell the story of the period and read as a continuum
rather than a snapshot. Every paragraph here is built from a `MarketPeriod`, which already carries
this period and the one before it, so continuity is in the facts and not in the phrasing.

Written as developed paragraphs. No lists, no headings inside the prose, and no source is ever named
- the report is HTC Global's own market research and says so once, on its first page.

Deliberately not written by a model. Every number in a customer's document can be traced to a line
in `period_report`, and a sentence assembled from those numbers cannot drift from them. The variety
comes from which facts a market actually offers - whether it turned, how far it ranged, how long it
has been quiet - rather than from invention.
"""
from __future__ import annotations

from raw_material_forecasting import units
from datetime import date
from typing import Sequence

from raw_material_forecasting.chemicals import display_chemical
from raw_material_forecasting.intelligence_register import source_free
from raw_material_forecasting.period_report import MarketPeriod, Period, day_in_words

# Below this, a move is noise rather than news, and the report says the market held.
FLAT = 0.5


def money(value: float | None, currency: str) -> str:
    if value is None:
        return "-"
    return units.with_unit(f"{currency} {value:,.0f}")


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{abs(value):.1f} per cent"


def _periods(count: int, kind: str) -> str:
    unit = "month" if kind == "month" else "quarter"
    if count <= 1:
        return f"the previous {unit}"
    return f"{count} {unit}s"


def _joined(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def upstream_sentence(feedstock: str, relations: Sequence[tuple[str, str]]) -> str:
    """Why this material is in a report nobody buys it from, and how far under the market it sits.

    The store knows that benzene reaches pMDI through aniline rather than directly. A report that
    calls every feedstock "one step upstream" tells a buyer something the first chemist he asks will
    contradict, so the step is carried into the sentence where it is known.
    """
    if not relations:
        return ""
    by_note: dict[str, list[str]] = {}
    for raw_material, note in relations:
        by_note.setdefault(str(note or "").strip(), []).append(display_chemical(raw_material))
    clauses = []
    for note, names in by_note.items():
        joined = _joined(list(dict.fromkeys(names)))
        clauses.append(f"{joined}, {note}" if note else joined)
    return f"{_joined(clauses)} is made from {display_chemical(feedstock)}."


def opening_paragraphs(markets: Sequence[MarketPeriod], period: Period, currency: str,
                       *, condition=None, behind=None, wording=None) -> list[str]:
    """The context a reader needs before any figure, from the period's own intelligence.

    Stefan, 10 October 2026, looking at the first edition the application produced: the opening
    came out as three stray sentences, two of them the register's own present-tense headlines.
    A heading-fragment is not an introduction. This is one paragraph, and it carries a recorded
    observation only where that observation has been written as a sentence a customer can read.
    """
    held = dict(wording or {})
    clauses: list[str] = []
    event = str(getattr(condition, "event", "") or "").strip()
    if event:
        clauses.append(f"{period.name} ran under the {event}")

    said: list[str] = []
    for entry in [one for items in (behind or {}).values() for one in items]:
        written = " ".join(str(held.get(_entry_key(entry), "") or "").split()).rstrip(".")
        if written:
            said.append(written[0].lower() + written[1:])
    if said:
        clauses.append(("against it " if event else "") + _joined(said[:2]))

    out = []
    if clauses:
        out.append(". ".join(part[0].upper() + part[1:] for part in clauses) + ".")
    named = (_joined([f"{display_chemical(row.chemical)} in {row.geography}" for row in markets])
             if len(markets) <= 3 else f"{_count_in_words(len(markets))} markets")
    # No period phrase and no bare count: "8 markets over Q3 2026" was a figure the facts never
    # supplied, scoped to a quarter most of those series did not cover (14 October 2026).
    out.append(f"This edition records {named}, in {currency} per {units.MT}, "
               "with the chain each material is made from.")
    return [" ".join(out)]


_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty")


def _count_in_words(count: int) -> str:
    return _NUMBER_WORDS[count] if 0 <= count < len(_NUMBER_WORDS) else f"{count:,}"


def _entry_key(entry) -> str:
    from raw_material_forecasting.development_writer import key_for

    return key_for(entry)


def expectation_paragraph(outlooks, period: Period) -> str:
    """Where the markets are expected next. An expectation, and nothing a reader is told to do."""
    said = [outlook_paragraph(one, period) for one in (outlooks or {}).values()]
    return " ".join(one for one in said if one)


def market_paragraph(row: MarketPeriod, period: Period) -> str:
    """What one market did in the period, and how that follows from the period before it."""
    row = row.with_period(period)
    where = f"{display_chemical(row.chemical)} in {row.geography}"
    if row.quiet:
        if not row.last_day:
            return f"{where} is not priced in this edition."
        return (f"{where} last traded at {money(row.last_price, row.currency)} on "
                f"{day_in_words(row.last_day)} and has not moved since.")

    opened = money(row.opened, row.currency)
    closed = money(row.closed, row.currency)
    if row.readings == 1:
        # One reading is a price, not a movement, and is said as one.
        return f"{where} carries a single reading, {closed} {row.scope_label}."
    moved = row.change_percent or 0.0
    # The change as the two prices on the page differ, not as the unrounded ones do. A reader who
    # subtracts 870 from 881 and is told the change was 10 stops trusting the rest of the document;
    # all three figures are rounded to the whole unit, so the one in the sentence is too.
    shown_change = abs(round(row.closed or 0.0) - round(row.opened or 0.0))

    # The scope comes from the row. Stefan, 10 October 2026: the toluene figure was right and the
    # word "quarter" on it was false, so nothing here composes a phrase that places a number in
    # time. `scope_label` is built in `period_report` from the observation dates and used whole.
    scope = row.scope_label
    if row.direction == "held":
        first = (f"{where} held steady {scope}, opening at {opened} and closing at {closed}, "
                 f"a movement of under one per cent.")
    else:
        verb = "rose" if row.direction == "rose" else "fell"
        first = (f"{where} {verb} {_percent(moved)} {scope}, from {opened} to {closed}, "
                 f"a change of {money(shown_change, row.currency)}.")

    second = ""
    if row.high is not None and row.low is not None and row.high != row.low:
        second = (f" {row.window_label} it ranged between {money(row.low, row.currency)} and "
                  f"{money(row.high, row.currency)}.")

    # A three-reading quarter and a ninety-reading quarter are not the same comparison, and the
    # sentence says which one it is.
    before = row.comparison_label
    word = "month" if period.kind == "month" else "quarter"
    third = {
        "continued": (f" This continued the direction of {before}, when the market moved "
                      f"{_percent(row.previous_change)} the same way."),
        "reversed": (f" This reversed {before}, which closed "
                     f"{_percent(row.previous_change)} in the other direction."),
        "settled": f" The market settled after moving {_percent(row.previous_change)} in {before}.",
        "steady": (f" It was equally quiet in {before}, so this is a market at rest."),
        "began": f" The {word} before it was flat, so the move began here.",
        "unknown": (f" The {word} before it holds no price for this market, so the move stands "
                    "on its own."),
    }[row.against_previous]
    return first + second + third


def outlook_paragraph(outlook, period: Period) -> str:
    """Where the market is expected next, with the range the method has earned and nothing more.

    The range is the one thing here that is not a forecast: it is a measurement of how far past
    forecasts for this market at this distance actually landed from the outcome. Saying how many
    were measured is what separates it from a confident guess.
    """
    if outlook is None or not outlook.stands_up:
        return ""
    weeks = int(outlook.horizon_weeks or 4)
    ahead = {4: "a month", 8: "two months", 12: "three months"}.get(weeks, f"{weeks} weeks")
    where = f"{display_chemical(outlook.chemical)} in {outlook.geography}"
    # Stefan's own wording, 9 October 2026. Two sentences where mine had three: the trials and the
    # disclaimer were explaining the method to a reader who is buying a price, and how the range is
    # arrived at is told once in About this report rather than under every market.
    if not outlook.has_band:
        # No band is better than a made-up one, and silence is worse than either.
        return (f"{where} is expected at {money(outlook.value, outlook.currency)} {ahead} out. "
                f"The range around that figure has not been measured for this market yet.")
    return (f"{where} is expected at {money(outlook.value, outlook.currency)} {ahead} out, "
            f"between {money(outlook.low, outlook.currency)} and "
            f"{money(outlook.high, outlook.currency)}.")


def background_paragraph(background, period: Period) -> str:
    """What the market is made of, before anything about its price.

    Stefan, 9 October 2026: "you have the actual signals in the application. You can use these
    signals for a background story." The plant roster, the pipeline and the dated events have been
    held market by market for weeks and the customer report has never read them.
    """
    if background is None or not background.plants:
        return ""
    where = f"{display_chemical(background.chemical)} in {background.geography}"
    count = len(background.plants)
    if background.capacity_is_a_range:
        combined = (f"{background.capacity_low_kt:,.0f} to "
                    f"{background.capacity_kt:,.0f} kt a year")
    else:
        combined = f"{background.capacity_kt:,.0f} kt a year"
    first = (f"{where} is made at {count} plant{'' if count == 1 else 's'} with a combined "
             f"{combined}.")
    owners = background.owners[:3]
    if owners:
        names = _joined([owner for owner, _kt in owners])
        share = background.top_share(len(owners))
        held = ("hold" if len(owners) > 1 else "holds")
        first += (f" {names} {held} {share:.0f} per cent of it"
                  + (", so a single turnaround moves the balance." if share >= 60
                     else ", and no single producer sets the balance alone."))
    return first


def coming_paragraph(background, today: date) -> str:
    """Capacity under way, where it lands inside the next few years."""
    from raw_material_forecasting.market_background import coming_within

    if background is None:
        return ""
    coming = coming_within(background, of=today)
    if not coming:
        return ""
    total = sum(row.kt for row in coming)
    share = (total / background.capacity_kt * 100.0) if background.capacity_kt else 0.0
    share_high = ((total / background.capacity_low_kt * 100.0)
                  if background.capacity_low_kt else share)
    parts = []
    for row in coming:
        year = str(row.due)[:4]
        site = f"{row.site} {row.owner}" if row.site else row.owner
        parts.append(f"{row.kt:,.0f} kt from {site} in {year}")
    said = f"{_joined(parts)} {'is' if len(parts) == 1 else 'are'} under way"
    if share:
        said += (f", together {share:.0f} to {share_high:.0f} per cent"
                 if round(share) != round(share_high) else f", together {share:.0f} per cent")
        said += " of what the region makes today"
    return said[0].upper() + said[1:] + "."


def events_paragraph(background, period: Period) -> str:
    """What happened to the plants themselves inside the period."""
    if background is None or not background.events:
        return ""
    said = [row["development"].rstrip(".") for row in background.events[:4] if row["development"]]
    if not said:
        return ""
    return " ".join(f"{one}." for one in said)


def pass_through_paragraph(market: MarketPeriod, feedstock: MarketPeriod,
                           period: Period) -> str:
    """The material and the feedstock under it, side by side over the same period."""
    if market is None or feedstock is None:
        return ""
    here, below = market.change_percent, feedstock.change_percent
    if here is None or below is None:
        return ""
    what = display_chemical(market.chemical)
    under = display_chemical(feedstock.chemical)
    here_said = market.scope_label or f"over {period.name}"
    below_said = feedstock.scope_label or f"over {period.name}"
    # Two markets read over different stretches of days are stated separately, each with its own
    # window. Charlie, 10 October 2026: "These movements can be discussed together, but their
    # percentages cannot be presented as directly comparable quarterly movements." This sentence
    # was the one doing it - "TDI and Toluene moved almost together over Q3 2026, 11.6 per cent
    # against 12.0 per cent", where toluene was read from 5 September.
    # Stefan, 11 October 2026: "the subsequent comparison says that PO 'moved 1.9%', omitting the
    # direction." `_percent` prints the size without the sign, because every sentence around it
    # says rose or fell in words. This one said "moved" and so said nothing.
    def went(value: float) -> str:
        if value > 0:
            return "rose"
        return "fell" if value < 0 else "held at"

    if here_said != below_said:
        return (f"{what} {went(here)} {_percent(here)} {here_said}. "
                f"{under} {went(below)} {_percent(below)} {below_said}.")
    gap = abs(here - below)
    if gap <= 2.0 and (here > 0) == (below > 0):
        reading = (f"{what} and {under} moved almost together {here_said}, "
                   f"{what} {went(here)} {_percent(here)} and {under} {went(below)} "
                   f"{_percent(below)}.")
    elif abs(below) > abs(here):
        reading = (f"{under} {went(below)} {_percent(below)} {below_said} against "
                   f"{_percent(here)} for {what}, which {went(here)}.")
    else:
        reading = (f"{what} {went(here)} {_percent(here)} {here_said} against "
                   f"{_percent(below)} for {under}, which {went(below)}.")
    return reading


def behind_paragraph(entry, period: Period, said: str = "") -> str:
    """One recorded development, written as a customer reads it.

    Stefan, 8 October 2026: a confirmed item is stated plainly, and a market indication is stated as
    an indication. The register never hands anything else to this function, so there is no third
    case; what is not confirmed and not indicated does not reach a document at all.

    Nothing here is composed. The headline is the sentence HTC Global wrote in its own briefing, the
    context is that briefing's own reading of why it matters, and the date was read out of the text.
    """
    day = entry.day
    when = f", {day_in_words(day.isoformat())}" if day else ""
    # Through the same scrubber the Market Intelligence page uses. `check_sources` already fails a
    # document that names an assessor, which is the right backstop and a poor first line: it stops
    # the report rather than cleaning the sentence, and the wording is not the writer's fault - it
    # is how the briefing quoted its own reading.
    headline = source_free(str(entry.headline or "")).rstrip(".")
    context = " ".join(source_free(str(entry.context or "")).split())
    # Stefan, 9 October 2026: "no meta leaking, disclosing of methods, etc. Show confidence."
    # A label saying how sure we are of our own research is a confession in two words. The sentence
    # carries its own register - an announcement reads as an announcement and an observation reads
    # as one - and an item that could not be established at all never reaches this function.
    lead = f"{headline}{when}."
    written = " ".join(str(said or "").split())
    if written:
        return f"{lead} {written}"
    return lead


# A sentence that tells the reader what to follow next belongs under What to watch. One that
# tells them what their position is now belongs on the first page. Printing the same paragraph in
# both, which is what the report did until 10 October 2026, says everything twice.
FORWARD = ("track", "watch", "monitor", "keep", "follow", "expect")


def _implications(entries) -> list[str]:
    said, parts = set(), []
    for entry in entries:
        whole = " ".join(str(getattr(entry, "implication", "") or "").split())
        for line in [part.strip() for part in whole.split(". ") if part.strip()]:
            line = line if line.endswith((".", "?", "!")) else line + "."
            if line.lower() in said:
                continue
            said.add(line.lower())
            parts.append(line)
    return parts


def implication_paragraph(entries, period: Period) -> str:
    """What the period's developments mean for someone buying this material, now."""
    return " ".join(line for line in _implications(entries)
                    if not line.lower().startswith(FORWARD))


def watch_paragraph(entries, period: Period) -> str:
    """What to follow in the period ahead, taken from the same developments."""
    return " ".join(line for line in _implications(entries)
                    if line.lower().startswith(FORWARD))


def condition_paragraph(condition, chemical: str, period: Period) -> str:
    """What was running through the chain over the period, stated as an observation.

    Stefan, 10 October 2026: a market being on high watch is not an explanation for a rise. This
    says what was happening and what the material is exposed to, and claims no causation it
    cannot show.
    """
    if condition is None:
        return ""
    event = str(getattr(condition, "event", "") or "").strip()
    if not event:
        return ""
    said = ""
    for exposure in list(getattr(condition, "exposures", ()) or ()):
        held = str(getattr(exposure, "chemicals", "") or "")
        if chemical not in [name.strip() for name in held.split(";") if name.strip()]:
            continue
        mechanism = str(getattr(exposure, "mechanism", "") or "").strip().rstrip(".")
        if mechanism:
            said = (f"Through the {event}, {display_chemical(chemical)} is exposed to "
                    f"{mechanism[0].lower() + mechanism[1:]}.")
        break
    return said

def spread_paragraph(gap: dict, period: Period) -> str:
    chemical = display_chemical(gap["chemical"])
    close = (f"{chemical} ended {period.name} dearest in {gap['dearest']} and cheapest in "
             f"{gap['cheapest']}, a difference of {money(gap['gap'], gap['currency'])}.")
    widened = gap.get("widened")
    if widened is None:
        return close + " The period opened without a price on both sides, so the change in that "\
                       "difference is not reported."
    if abs(widened) < 1:
        return close + " That difference is where it began the period."
    direction = "widened" if widened > 0 else "narrowed"
    return (close + f" The gap {direction} by {money(abs(widened), gap['currency'])} over the "
            "period, which is what a buyer weighing one region against another is looking at.")


def _continuity(row: MarketPeriod, period: Period) -> str:
    """How a single market stands against the period before it, for the summary of one market."""
    unit = "month" if period.kind == "month" else "quarter"
    return {
        "continued": f" It carried on in the direction it had taken in the previous {unit}.",
        "reversed": f" It turned against the direction of the previous {unit}.",
        "settled": f" It settled after moving in the previous {unit}.",
        "steady": f" It was equally quiet in the previous {unit}.",
        "began": " The previous period was flat, so the move began here.",
        "unknown": f" The preceding {unit} holds no price for this market, so the move is reported "
                   "without a comparison.",
    }.get(row.against_previous, "")


def executive_summary(markets: Sequence[MarketPeriod], facts: dict, period: Period,
                      condition=None, recorded: int = 0) -> str:
    """Half a page for the reader who goes no further."""
    if not markets:
        return (f"This edition covers {period.name}. No market in the selection carries a price for "
                "the period.")
    moved = facts["rose"] + facts["fell"]
    # One market counted three times over reads as a form letter. "Across the 1 markets covered in
    # Q3 2026, 1 rose, 0 fell and 0 held within one per cent. The largest single movement was pMDI
    # in China." - Stefan, 8 October 2026, reading a real report of one market.
    if facts["markets"] == 1 and not facts["quiet"]:
        only = markets[0]
        where = f"{display_chemical(only.chemical)} in {only.geography}"
        if only.quiet or only.change_percent is None:
            opening = f"This edition covers {where} over {period.name}."
        else:
            verb = {"rose": "rose", "fell": "fell"}.get(only.direction, "held")
            moved_by = (f" {verb} {_percent(only.change_percent)} to "
                        f"{money(only.closed, only.currency)}" if verb != "held"
                        else f" held within one per cent, closing at "
                             f"{money(only.closed, only.currency)}")
            opening = f"This edition covers one market. {where}{moved_by} over {period.name}."
        return opening + _continuity(only, period)
    opening = (f"Across the {facts['markets']} markets covered in {period.name}, "
               f"{facts['rose']} rose, {facts['fell']} fell and {facts['held']} held within one "
               "per cent.")
    if facts["quiet"]:
        quiet = facts["quiet"]
        opening += (f" A further {quiet} {'market' if quiet == 1 else 'markets'} "
                    f"{'saw' if quiet == 1 else 'saw'} no new developments in the period and "
                    f"{'is' if quiet == 1 else 'are'} shown at "
                    f"{'the price it' if quiet == 1 else 'the prices they'} last traded at.")

    biggest = facts.get("biggest")
    second = ""
    if biggest is not None and biggest.change_percent is not None:
        verb = "rise" if biggest.change_percent > 0 else "fall"
        second = (f" The largest single movement was {display_chemical(biggest.chemical)} in "
                  f"{biggest.geography}, a {verb} of {_percent(biggest.change_percent)} to "
                  f"{money(biggest.closed, biggest.currency)}.")

    third = ""
    if moved:
        if facts["continued"] and not facts["reversed"]:
            third = (" Every market that moved carried on in the direction it had taken in the "
                     "previous period.")
        elif facts["reversed"] and not facts["continued"]:
            third = (" Every market that moved turned against the direction of the previous "
                     "period.")
        elif facts["continued"] or facts["reversed"]:
            third = (f" Of those that moved, {facts['continued']} continued the previous period's "
                     f"direction and {facts['reversed']} turned against it.")

    # What was happening around the prices, for the reader who stops after this page.
    fourth = ""
    if condition is not None:
        state = str(getattr(condition, "state", "") or "").lower()
        event = str(getattr(condition, "event", "") or "")
        if state and event:
            fourth = (f" Through the period the supply chain itself was {state} following "
                      f"the {event}, and each material carries its own exposure below.")
        elif event:
            fourth = f" The {event} ran through the period and is reported under each material."
    return opening + second + third + fourth


def quiet_note(period: Period) -> str:
    """The standing sentence for the markets with nothing new, said once rather than per market."""
    return ("A market with nothing new in the period is shown at the price it last traded at, "
            "with the day it last traded on.")
