"""Which markets a customer page may offer. The one list the selectors read.

Stefan, 13 October 2026: "when there is nothing to show, there should be no dropdown choice ... So
now we move to a more controlled method. On the Data & Sources page I want the first table to show
which time series are eligible to show on the customer page."

A **market** is a chemical, a geography, a set of terms and a packaging: the four selectors above
the chart, and `populations.MARKETS`. Every market the store holds is a row, published or not, so a
market that is missing from the customer pages is in the table with the reason next to it. This
module said "time series" for that, which is one of the four things the application called a series
before masterplan B gave each population one word.

Each page column asks that page's own question, using the same function the page itself uses:

  Current Market       anybody priced it inside `CURRENT_WITHIN_DAYS` (six months)
  Historic Pricing     there is a drawn reading, and the series is not on Current Market
  Forecast             the newest drawn reading is in the forecast years
  Market Intelligence  a capacity roster or developments exist for the chemical and geography

"Show on customer page" is Yes when a price page draws the series. Market Intelligence is a fact
about the chemical and geography; it decides only for a market that has intelligence and no price
series, which is a row of its own.

A market is judged after the gluing, never piece by piece. Where a ruling in
`series_curation.ALIASES` reads one publisher's series on another's terms ("on overlap, merge, on
gap, bridge"), both land on the same row here, exactly as they land on the same line on the chart.
A piece that would not be eligible alone - a history that stopped in 2023 - can make a glued series
eligible, and the pieces are listed so that stays visible (Stefan, 13 October 2026: "Each individual
series may be not eligible but glued together they could very well be"). The customer selectors are
built from `offered()` below, so the table and the dropdowns cannot disagree: if a chemical has no
Yes anywhere on a page, that page does not offer it.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable, Sequence

from datetime import timedelta

from raw_material_forecasting.market_lines import (
    COLLECTED, CURRENT_WITHIN_DAYS, DRAWN_ON_CURRENT_DAYS, FORECAST_WITHIN_DAYS, _covers_to,
    _stated, drawable_selections, drawn_as_a_line, priced_recently, priced_since, provenance_of)
from raw_material_forecasting.source_status import fetched_ids
from raw_material_forecasting import customer_controls
from raw_material_forecasting.series_curation import (
    basis_for, control_key, is_drawn, market_key, ruling_for)

CURRENT = "Current Market"
HISTORIC = "Historic Pricing"
FORECAST = "Forecast"
INTELLIGENCE = "Market Intelligence"
PAGES = (CURRENT, HISTORIC, FORECAST, INTELLIGENCE)
# The three a reader can be given a price on, and the three Stefan can decide himself. Market
# Intelligence belongs to the chemical and geography rather than to the series, so it is not one.
PRICE_PAGES = (CURRENT, HISTORIC, FORECAST)
SHOW = "Show on customer page"
# Why a Yes on the Forecast column is not honoured. Not a judgement he is overruling: a reading
# older than the longest horizon leaves no month ahead to forecast.
TOO_OLD_TO_FORECAST = "Newest reading is older than the longest horizon; nothing ahead to forecast"

# Marks a cell whose hand-set answer the evaluation would no longer give.
#
# A choice is made against the market as it stood. Markets move: polyester polyol China was set to
# No on 13 October with too few readings, and four days later had 94 and a price from that week,
# with nothing on the page to say so. A feed can also stop, which turns an old Yes stale the other
# way. The mark says "the evaluation disagrees with you now", never that either is wrong - the
# whole point of the column is that Stefan outranks the evaluation (Stefan, 16 October 2026).
#
# It rides in the cell's own text because Streamlit styles only non-editable columns, and these
# three have to stay editable.
REVIEW_MARK = "⚠"


def mark(answer: bool, *, review: bool) -> str:
    return ("Yes" if answer else "No") + (f" {REVIEW_MARK}" if review else "")


def answer_of(cell: str) -> bool:
    """The Yes or No under a cell, with any review mark taken off."""
    return str(cell).replace(REVIEW_MARK, "").strip() == "Yes"


CELL_OPTIONS = ["Yes", "No", f"Yes {REVIEW_MARK}", f"No {REVIEW_MARK}"]

SET_ASIDE = "All readings set aside by a ruling"
NO_PRICE = "No priced reading"
HISTORY_ONLY = "Only history before the forecast years; not collected recently"

Key = tuple[str, str, str, str]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _day(value: Any) -> str:
    return str(value or "")[:10]


def offered(identities: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]],
            page: str, *, sources: Sequence[dict[str, Any]] | None = None,
            as_of: date | None = None, forecast_from_year: int = 2026) -> set[Key]:
    """The (chemical, geography, terms, packaging) a price page may offer. Nothing else.

    `identities` must already be the published, entitled ones: this decides what has something to
    show, not what a reader is allowed to see.
    """
    # Every price page draws a chart, and a chart needs a line. Asked in the window that page
    # draws, so the answer can differ between them: EO United States FOB holds 19 readings back to
    # 2003 and one of them inside the last year, which is a dot on Current Market and a line on
    # Historic Pricing (Stefan, 20 November 2026, on an empty chart under a filled card).
    def a_line(*, within_days: int | None) -> set[Key]:
        return drawn_as_a_line(identities, observations, as_of=as_of, within_days=within_days)

    def current_rule() -> set[Key]:
        """What Current Market offers before Stefan's own Yes and No are applied.

        Historic subtracts this rather than `offered(CURRENT)`: his No means "not on Current", not
        "move it to Historic", and the two pages are split by the evaluation.
        """
        return (priced_recently(identities, observations, as_of=as_of,
                                within_days=CURRENT_WITHIN_DAYS)
                & a_line(within_days=DRAWN_ON_CURRENT_DAYS))

    if page == CURRENT:
        # Priced inside six months, by anybody. `priced_recently` already asks `is_drawn`, so this
        # is a subset of `drawable_selections` and needs no intersection with it.
        found = current_rule()
    elif page == HISTORIC:
        # Everything else we can draw a line from, so the two pages stay exclusive. The whole
        # record is drawn here; Current Market draws one year, which is why this subtracts what
        # Current OFFERS rather than what was priced recently - a market Current turns away for
        # holding one reading this year can have twelve years behind it, and this is its page.
        found = a_line(within_days=None) - current_rule()
    elif page == FORECAST:
        # Priced in the forecast years AND recently enough that a horizon still lands ahead of
        # today. The year test alone let a market whose feed stopped in January head a card for
        # February, all the way into October.
        #
        # And a line, asked of the WHOLE record, because that is what this page's chart draws -
        # `_build_forecast_figure` is handed the governed history entire, not the year Current
        # Market cuts to. Southeast Asia CFR is the case: one reading inside the last year and a
        # record going back to 2023, which is a dot on Current Market and a real history here.
        found = (priced_since(identities, observations,
                              from_year=forecast_from_year, as_of=as_of)
                 & a_line(within_days=None))
    else:
        raise ValueError(f"not a price page: {page}")

    # Stefan's own Yes puts a series on this page where the evaluation said No.
    #
    # Except on Forecast, where a market whose newest reading is older than the longest horizon has
    # nothing ahead to forecast. That is arithmetic rather than a judgement, so it is not his to
    # overrule (16 October 2026). A Yes there is simply not honoured, and the table says why.
    put_on = customer_controls.shown(page)
    if put_on:
        # A Yes on CURRENT is held to the same kind of limit as a Yes on FORECAST (masterplan G).
        # He can overrule "we did not collect this ourselves"; he cannot overrule "this price is
        # 3,586 days old", which is what put aliphatic polyester polyol Europe DAP, last priced on
        # 1 January 2017, onto the Current Market page.
        allowed = None
        if page == FORECAST:
            allowed = (priced_since(identities, observations, from_year=forecast_from_year,
                                    as_of=as_of)
                       & a_line(within_days=None))
        elif page == CURRENT:
            # The same set the page itself is built from, since 3 November 2026. A Yes on Current
            # can therefore no longer ADD a market - the only test left there is the age, and that
            # is precisely the one he may not overrule. It is kept rather than dropped so the
            # refusal is still made here and the table can say why. Checked against his own three
            # Yes entries that day: two are on the page by the six-month rule anyway, and the third
            # is aliphatic polyester polyol Europe DAP, last priced on 1 January 2017, which the
            # old 62-day limit was already refusing. Nothing he set is lost. His No is untouched.
            allowed = current_rule()
        for one in drawable_selections(identities, observations):
            if control_key(one) in put_on and (allowed is None or one in allowed):
                found = found | {one}

    # And his No takes it off this page, whatever the evaluation said.
    taken_off = customer_controls.hidden(page)
    if taken_off:
        found = {one for one in found if control_key(one) not in taken_off}
    return found


def chemicals_offered(selections: Iterable[Key]) -> list[str]:
    return sorted({one[0] for one in selections if one[0]})


def packagings_offered(selections: Iterable[Key], chemical: str, geography: str,
                       terms: str) -> list[str]:
    wanted = _text(terms).upper()
    return sorted({one[3] for one in selections
                   if one[0] == _text(chemical) and one[1] == _text(geography)
                   and one[2].upper() == wanted})


def intelligence_markets(chemicals: Iterable[str], geographies: Iterable[str],
                         has_intelligence: Callable[[str, str], bool] | None) -> set[tuple[str, str]]:
    if has_intelligence is None:
        return set()
    taken_off = customer_controls.hidden()
    return {(str(c), str(g)) for c in chemicals for g in geographies if has_intelligence(c, g)
            and (str(c), str(g), "", "") not in taken_off}


def catalogue(identities: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]],
              **settings: Any) -> list[dict[str, Any]]:
    """The table: Stefan's choices applied, with what the evaluation alone says beside them."""
    with customer_controls.suspended():
        evaluated = {series_key(row): row for row in _catalogue(identities, observations, **settings)}
    choices = customer_controls.choices()
    rows = _catalogue(identities, observations, **settings)
    for row in rows:
        k = series_key(row)
        base = evaluated.get(k)
        row["evaluated"] = bool(base[SHOW]) if base else False
        row["evaluated_reason"] = base["reason"] if base else ""
        # What the evaluation alone says for each page, and what Stefan has said about it. Three
        # answers rather than one since 16 October 2026: a series can belong on Historic Pricing
        # and not on Forecast, and one column could not say that.
        row["evaluated_pages"] = {page: bool(base[page]) if base else False
                                  for page in PRICE_PAGES}
        by_hand = customer_controls._pages_of(choices.get(k) or {})
        row["by_hand_pages"] = {page: by_hand.get(page) for page in PRICE_PAGES}
        # Where the evaluation has MOVED since the choice was made - not merely where it disagrees.
        # Disagreement is the normal state of a standing judgement: a series set to No for having
        # too few readings disagrees with the evaluation permanently, and marking that says nothing.
        # A choice written before the baseline existed has none, and is left unmarked rather than
        # guessed at.
        was = customer_controls.baseline_of(choices.get(k) or {})
        row["needs_review"] = {page: (page in was
                                      and by_hand.get(page) is not None
                                      and was[page] != row["evaluated_pages"][page])
                               for page in PRICE_PAGES}
        choice = choices.get(k)
        row["by_hand"] = None if choice is None else bool(row[SHOW])
        # Why the choice was made, as recorded when it was made. `set_choice` has always stored it
        # and the table has never shown it, so a row read "Set by hand; the evaluation says Yes"
        # and left the reader to remember why - which is the whole value of having written it down.
        row["by_hand_reason"] = str((choice or {}).get("reason") or "").strip()
    return rows


def series_key(row: dict[str, Any]) -> str:
    return customer_controls.key(row["chemical"], row["geography"], row["terms"], row["packaging"])


def _catalogue(identities: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]], *,
              is_published: Callable[[dict], bool],
              why_not_published: Callable[[dict], str],
              has_intelligence: Callable[[str, str], bool] | None = None,
              chemicals: Sequence[str] = (), geographies: Sequence[str] = (),
              publisher_of: Callable[[str], str] = str,
              sources: Sequence[dict[str, Any]] | None = None,
              as_of: date | None = None, forecast_from_year: int = 2026) -> list[dict[str, Any]]:
    """One row per market the store holds, with each page's answer and the final verdict.

    `sources` says which sources we fetch, which is how "did we collect this?" is answered since
    masterplan G. Without it no reading counts as ours and the Current column is empty - loud rather
    than quietly wrong, which is what the date rule it replaced was.
    """
    published = [row for row in identities if is_published(row)]
    published_keys = {_text(row.get("identity_key")) for row in published}
    collected_from = fetched_ids(sources)
    current = offered(published, observations, CURRENT, sources=sources, as_of=as_of)
    historic = offered(published, observations, HISTORIC, sources=sources, as_of=as_of)
    # `as_of` on this one too. Without it the Forecast column was judged against the wall clock
    # while the two beside it were judged against the day asked for, so this table and the Forecast
    # page's own dropdown could disagree about the same series - which is what
    # `test_the_dropdowns_agree_with_the_table` exists to catch, and did, on 31 October 2026 when
    # the fixture's readings finally crossed the 91-day line and the two answers came apart.
    ahead = offered(published, observations, FORECAST,
                    forecast_from_year=forecast_from_year, as_of=as_of)

    by_key = {_text(row.get("identity_key")): row for row in identities
              if _text(row.get("identity_key"))}
    held: dict[Key, dict[str, Any]] = {}
    for row in observations:
        identity = by_key.get(_text(row.get("identity_key")))
        if identity is None:
            continue
        source_id = _text(row.get("source_id"))
        key = market_key(identity, source_id)
        one = held.setdefault(key, {"readings": 0, "drawn": 0, "priced": 0, "first": "",
                                    "last": "", "publishers": set(), "types": set(),
                                    "reasons": set(), "published": False, "set_aside": 0,
                                    "pieces": {}, "aside_months": set()})
        one["readings"] += 1
        one["publishers"].add(_text(publisher_of(source_id)))
        one["types"].add(_text(identity.get("observation_type")))
        if _text(identity.get("identity_key")) in published_keys:
            one["published"] = True
        else:
            one["reasons"].add(why_not_published(identity))
        day = _day(row.get("observation_date"))
        if row.get("price") is None or not day:
            continue
        one["priced"] += 1
        one["held_first"] = min(one.get("held_first") or day, day)
        one["held_last"] = max(one.get("held_last") or "", day)
        if not is_drawn(identity, source_id, day):
            one["set_aside"] += 1
            one["aside_months"].add(day[:7])
            cut = ruling_for(identity, source_id, day)
            if cut is not None:
                one.setdefault("rulings", set()).add(
                    f"{publisher_of(source_id)}: " + (
                        f"ruling covers readings to {cut.judged_until}; newer ones are judged again"
                        if cut.judged_until else "standing ruling (duplicate or merged series)"))
            continue
        one["drawn"] += 1
        one.setdefault("drawn_months", set()).add(day[:7])
        piece = one["pieces"].setdefault(
            (publisher_of(source_id), _text(identity.get("commercial_basis"))),
            {"readings": 0, "first": day, "last": day, "collected": ""})
        piece["readings"] += 1
        piece["first"] = min(piece["first"], day)
        piece["last"] = max(piece["last"], day)
        if provenance_of(source_id, collected_from) == COLLECTED:
            piece["collected"] = max(piece["collected"], day)
        one["first"] = min(one["first"] or day, day)
        one["last"] = max(one["last"], day)

    # Which months each market already draws, across all its terms: what a set-aside series would
    # add to the historic view if it were brought back (Stefan, 13 October 2026: bring it back when
    # it improves the historic view or the forecast).
    drawn_months: dict[tuple[str, str], set[str]] = {}
    for key, one in held.items():
        drawn_months.setdefault((key[0], key[1]), set()).update(
            m for p in [one] for m in _months_of(p))
    for key, one in held.items():
        one["adds_months"] = len(one["aside_months"] - drawn_months.get((key[0], key[1]), set()))

    told = intelligence_markets(chemicals, geographies, has_intelligence)
    out = []
    for key, one in held.items():
        pages = {CURRENT: key in current, HISTORIC: key in historic, FORECAST: key in ahead,
                 INTELLIGENCE: (key[0], key[1]) in told}
        out.append(_row(key, one, pages, as_of=as_of, forecast_from_year=forecast_from_year))
    # A market with intelligence and no price series still has a page to show.
    priced_markets = {(key[0], key[1]) for key in held}
    for chemical, geography in sorted(told - priced_markets):
        out.append(_row((chemical, geography, "", ""), None,
                        {CURRENT: False, HISTORIC: False, FORECAST: False, INTELLIGENCE: True}))
    out.sort(key=lambda r: (r["chemical"].lower(), r["geography"].lower(), r["terms"], r["packaging"]))
    return out


def _months_of(one: dict[str, Any]) -> set[str]:
    return set(one.get("drawn_months", ()))


def _pieces(key: Key, one: dict[str, Any] | None, *, as_of: date | None,
            forecast_from_year: int) -> list[dict[str, Any]]:
    """The publisher series glued into this time series, each with what it would earn alone."""
    if not one:
        return []
    today = as_of or date.today()
    cutoff = (today - timedelta(days=CURRENT_WITHIN_DAYS)).isoformat()
    # The same bound the glued row is judged on. This test used to be the year alone, so a piece
    # that stopped in January read "Forecast alone: Yes" beside a row that said No.
    ahead = (today - timedelta(days=FORECAST_WITHIN_DAYS)).isoformat()
    out = []
    for (publisher, published_as), piece in sorted(one["pieces"].items(),
                                                   key=lambda kv: kv[1]["first"]):
        # The same question the page now asks: priced inside six months, by anybody. It used to
        # require `piece["collected"]`, so a publisher assessing this market last week read No.
        current = bool(piece["last"]) and _covers_to(piece["last"]) >= cutoff
        forecast = (piece["last"][:4].isdigit() and int(piece["last"][:4]) >= forecast_from_year
                    and _covers_to(piece["last"]) >= ahead)
        out.append({"publisher": publisher, "published_as": published_as,
                    "read_as": key[2], "readings": piece["readings"],
                    "first": piece["first"], "last": piece["last"],
                    CURRENT: current, FORECAST: forecast})
    return out


def _value_if_restored(one: dict[str, Any] | None) -> str:
    """What the set-aside readings would bring back. Stefan, 13 October 2026: a series comes back
    when it A) improves the historic view or lets a method be backtested, or B) improves forecast
    accuracy. The table measures A. B needs a backtest and is not claimed here."""
    if not one or not one.get("set_aside"):
        return ""
    months = len(one.get("aside_months", ()))
    adds = int(one.get("adds_months", 0))
    unit = "month" if months == 1 else "months"
    if adds:
        return f"Adds {adds} of its {months} {unit} that the market has no reading for"
    return (f"Adds no month: the market already has a reading in its {unit}" if months == 1
            else f"Adds no month: the market already has a reading in all {months} of its months")


def _row(key: Key, one: dict[str, Any] | None, pages: dict[str, bool], *,
         as_of: date | None = None, forecast_from_year: int = 2026) -> dict[str, Any]:
    # A price series is shown when a price page draws it. Market Intelligence belongs to the
    # chemical and geography, not to the series: it is marked on every series of that market, but
    # it does not put a series nobody draws on a customer page. A market with intelligence and no
    # price series is its own row, and there it decides.
    priced = any(pages[p] for p in (CURRENT, HISTORIC, FORECAST))
    shown = priced if one is not None else pages[INTELLIGENCE]
    reason = ""
    if one is not None and not priced:
        if not one["published"]:
            reason = "; ".join(sorted(r for r in one["reasons"] if r)) or "Not a published market"
        elif not one["priced"]:
            reason = NO_PRICE
        elif not one["drawn"]:
            reason = SET_ASIDE + (" - " + "; ".join(sorted(one.get("rulings", ())))
                                  if one.get("rulings") else "")
        else:
            reason = HISTORY_ONLY
    return {
        "chemical": key[0], "geography": key[1], "terms": key[2], "packaging": key[3],
        "types": sorted(t for t in (one or {}).get("types", ()) if t),
        "publishers": sorted(p for p in (one or {}).get("publishers", ()) if p),
        "readings": (one or {}).get("drawn", 0),
        "held": (one or {}).get("priced", 0),
        "held_first": (one or {}).get("held_first", ""),
        "held_last": (one or {}).get("held_last", ""),
        "first": (one or {}).get("first", ""), "last": (one or {}).get("last", ""),
        **pages,
        "reason": reason,
        # Whether a Yes on the Forecast column could be honoured at all. Read off the newest drawn
        # reading, which is the same figure `priced_since` judges.
        "forecast_too_old": bool(
            one and one.get("last")
            and _covers_to(one["last"]) < ((as_of or date.today())
                                           - timedelta(days=FORECAST_WITHIN_DAYS)).isoformat()),
        "set_aside": (one or {}).get("set_aside", 0),
        "value_if_restored": _value_if_restored(one),
        "pieces": _pieces(key, one, as_of=as_of, forecast_from_year=forecast_from_year),
        SHOW: shown,
    }


SHORT_PAGE = {CURRENT: "Current", HISTORIC: "Historic", FORECAST: "Forecast",
              INTELLIGENCE: "Intelligence"}


def remarks(row: dict[str, Any]) -> str:
    """Why a series is not shown, or what is held back from one that is. Empty when nothing is."""
    said = []
    if row.get("by_hand") is not None:
        verdict = "Yes" if row.get("evaluated") else "No"
        # The reason leads where there is one, because that is what a reader needs; the mechanism
        # follows it in lower case as a continuation of the same sentence.
        why = row.get("by_hand_reason") or ""
        if why:
            said.append(why)
        said.append(("set" if why else "Set") + f" by hand; the evaluation says {verdict}"
                    + (f" ({row['evaluated_reason']})" if row.get("evaluated_reason") else ""))
        if row["by_hand"] and not row[SHOW]:
            said.append("nothing to draw: no priced reading")
        return "; ".join(said)
    said += [row["reason"]] if row["reason"] else []
    if row.get("set_aside"):
        if row["reason"] != SET_ASIDE and not row["reason"].startswith(SET_ASIDE):
            said.append(f"{row['set_aside']} readings set aside by a ruling")
        if row.get("value_if_restored"):
            said.append(row["value_if_restored"])
    return "; ".join(said)


def rows_for_display(rows: Sequence[dict[str, Any]], *, chemical_name=str, geography_name=str,
                     terms_name=None, packaging_name=str) -> list[dict[str, Any]]:
    """The table as Stefan reads it (13 October 2026): the verdict first, then only what is needed
    to recognise the series and judge the verdict.

    Ten columns, cut from eighteen. The four page columns are one "Pages" cell; Set aside, Value if
    restored and Why not are one "Remarks" cell; Price type is in Remarks when it is the reason;
    Glued from lives in the piece-by-piece table below.

    First and last were glued into one "Period" cell reading "2016-06 to 2026-10". Stefan,
    16 October 2026: "split the column period in 2 columns; from and to." A single cell cannot be
    sorted on either end, and which end matters depends on the question - the oldest history for
    Historic Pricing, the newest reading for Forecast.
    """
    out = []
    for row in rows:
        terms = (terms_name(row["chemical"], row["geography"])(row["terms"])
                 if terms_name and row["terms"] else (row["terms"] or "-"))
        first = row["first"] or row.get("held_first") or ""
        last = row["last"] or row.get("held_last") or ""
        out.append({
            # One column per price page since 16 October 2026, each his to set. The single "Show on
            # customer page" cell decided all three together, and could not say that a series
            # belongs on Historic Pricing but not on Forecast.
            **{SHORT_PAGE[page]: mark(bool(row[page]),
                                     review=bool((row.get("needs_review") or {}).get(page)))
               for page in PRICE_PAGES},
            "Chemical": chemical_name(row["chemical"]),
            "Geography": geography_name(row["geography"]) if row["geography"] else "-",
            "Terms": terms,
            "Packaging": packaging_name(row["packaging"]) if row["terms"] else "-",
            "Intelligence": "Yes" if row[INTELLIGENCE] else "No",
            "Publishers": ", ".join(row["publishers"]) or "-",
            "Readings": int(row.get("held", row["readings"])),
            "From": first[:7] if first else "-",
            "To": last[:7] if last else "-",
            "Remarks": remarks(row),
        })
    return out


def glued_from(row: dict[str, Any]) -> str:
    """The pieces of a glued market in one cell. Empty when it is one publisher's own."""
    pieces = row.get("pieces") or []
    if len(pieces) < 2 and not any(p["published_as"] != p["read_as"] for p in pieces):
        return ""
    return " + ".join(
        f"{p['publisher']}"
        + (f" ({p['published_as']} read as {p['read_as']})" if p["published_as"] != p["read_as"] else "")
        + f" {p['first'][:7]} to {p['last'][:7]}"
        for p in pieces)


def pieces_for_display(rows: Sequence[dict[str, Any]], *, chemical_name=str,
                       geography_name=str) -> list[dict[str, Any]]:
    """Every glued market broken into its pieces, with what each piece would earn alone.

    The point of the table: a piece that is No alone and part of a market that is Yes.
    """
    out = []
    for row in rows:
        pieces = row.get("pieces") or []
        if not glued_from(row):
            continue
        for p in pieces:
            out.append({
                "Chemical": chemical_name(row["chemical"]),
                "Geography": geography_name(row["geography"]),
                "Terms": row["terms"] or "-", "Packaging": row["packaging"] or "-",
                "Piece": p["publisher"], "Published as": p["published_as"] or "-",
                "Readings": p["readings"], "First": p["first"], "Last": p["last"],
                "Current alone": "Yes" if p[CURRENT] else "No",
                "Forecast alone": "Yes" if p[FORECAST] else "No",
                "Market shown": "Yes" if row[SHOW] else "No",
            })
    return out
