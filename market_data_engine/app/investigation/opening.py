"""Turning what the engine already rejected into cases, and working the free enquiries.

Two inputs today, both of them things the application was throwing away.

An event the signal lane refused to publish because no actor is named: that is a case with an ACTOR
gap, and it is the one Stefan described - "if there are 4 companies with TDI in Northwest then you
check their websites."

A price series whose level is impossible for its own chemical: that is a case too, and the gap is
not the actor but the MARKET - whether anyone prices this material in this geography at all. The
ten Comtrade series found on 17 October would each have opened one, and terephthalic acid South
Korea, which the tonnage floor did not fix, would still be open with its fifth question pending
instead of being suppressed and forgotten.

Nothing here decides a case. It opens them and runs the enquiries that cost nothing - our own
roster, our own prices - because those answer without a network, without a publisher and without
anybody's permission. What is left pending is what needs a fetch or a person, and it is visible as
pending rather than as silence.
"""
from __future__ import annotations

import json
import statistics as st
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EventRecordModel, MarketIdentityModel, PriceObservationModel
from app.investigation import model as m
from app.investigation import our_prices, roster, store

# The engine's own vocabulary for "one publisher and nobody named", which is what the signal lane
# rejects. See app.producer_signals.verification.
UNATTRIBUTED = "UNATTRIBUTED"

# A price bulletin is not somebody doing something. Longzhong publishes regional and export price
# flashes - 价格快讯, 价格行情, 市场日报 - and the connector files them as
# `producer_price_announcement`, which is how "[聚醚]：10月13日东南亚地区聚醚多元醇价格快讯"
# ("Southeast Asia polyether polyol price bulletin") came to be rendered as "a polyether polyol
# producer price update was issued" and then rejected for naming no producer.
#
# These are not cases. We know exactly what they are: an assessment of a market we price ourselves,
# misfiled as an event. Opening a case for each would be 93 investigations into a classification
# bug. They are counted and reported instead, and the classification is the connector's to fix.
_BULLETIN_WORDS = ("价格快讯", "价格行情", "市场日报", "早报", "日评", "周评", "行情",
                   "数据统计", "动态汇总", "情况汇总", "周度数据")


def looks_like_an_assessment(headline: str) -> bool:
    """Whether a headline is a publisher's own price bulletin or statistical roundup."""
    return any(word in str(headline or "") for word in _BULLETIN_WORDS)


# What the engine calls a material against what our roster and our price store call a chemical.
# `POLYOL` from a Chinese headline is polyether polyol; without this the case opens with no
# chemical and every enquiry that needs one answers "not enough to look it up".
MATERIAL_AS_CHEMICAL = {"POLYOL": "POLYETHER_POLYOL", "MDI": "PMDI", "PURE_MDI": "PURE_MDI",
                        "PMDI": "PMDI", "TDI": "TDI", "PO": "PO", "EO": "EO",
                        "TOLUENE": "TOLUENE", "BENZENE": "BENZENE", "ANILINE": "ANILINE",
                        "ADIPIC_ACID": "ADIPIC_ACID", "LIQUID_CHLORINE": "CHLORINE"}


def chemical_of(material: str) -> str:
    code = str(material or "").strip().upper()
    return MATERIAL_AS_CHEMICAL.get(code, code)


def _headline(data) -> str:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return data
    return str((data or {}).get("headline") or "").strip()


def _geography(data) -> str:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return ""
    return str((data or {}).get("market_geography") or "").strip()


def from_unattributed_events(session: Session, *, since: datetime | None = None) -> list:
    """Open an ACTOR case for every event the signal lane could not attribute.

    The headline is carried VERBATIM, in its own language. A generated English summary is what made
    "a polyether polyol producer price update was issued" out of a headline that said "Southeast
    Asia regional price bulletin", and a case reasoned from the summary investigates the wrong
    thing.
    """
    query = select(EventRecordModel)
    if since is not None:
        query = query.where(EventRecordModel.available_from >= since)
    opened = []
    for event in session.execute(query).scalars().all():
        headline = _headline(event.data)
        if not headline:
            # An event with no headline at all is still a case - two force majeure records on TDI,
            # 6 and 7 September 2026, carry no text whatever. The gap there is SCOPE: we do not
            # know what it said.
            opened.append(store.open_case(session, m.Case(
                gap=m.SCOPE, observed=f"[no headline recorded] {event.event_type} {event.material or ''}".strip(),
                observed_at=event.available_from, source_id=event.source_id,
                evidence_id=event.evidence_id or "", origin_kind="EVENT", origin_ref=event.id,
                chemical=chemical_of(event.material), geography=_geography(event.data))))
            continue
        if looks_like_an_assessment(headline):
            # Known, not unknown. Counted by `assessments_misfiled` and left to the connector.
            continue
        if not _needs_an_actor(event):
            continue
        opened.append(store.open_case(session, m.Case(
            gap=m.ACTOR, observed=headline, observed_at=event.available_from,
            source_id=event.source_id, evidence_id=event.evidence_id or "",
            origin_kind="EVENT", origin_ref=event.id,
            chemical=chemical_of(event.material),
            geography=_geography(event.data) or "China")))
    return opened


def _needs_an_actor(event) -> bool:
    """Whether this event is about somebody doing something, with nobody named.

    Read off the event's own payload rather than the rendered sentence: `actor` is what the lane
    filled in, and an empty one with a PLANT_EVENT or a PRODUCER_PRICE is exactly the case.
    """
    data = event.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return False
    if str(event.event_type or "") not in ("PLANT_EVENT", "PRODUCER_PRICE", "FORCE_MAJEURE"):
        return False
    actor = str((data or {}).get("actor") or "").strip()
    return not actor or actor.lower() in ("unnamed producer", "unknown", "n/a")


def from_implausible_levels(session: Session, *, factor: float = 3.0) -> list:
    """Open a MARKET case for every series whose level is impossible for its own chemical.

    Compared within its own currency, because a CNY market is legitimately seven times a USD one.
    A spread is checked as well as a level: ethylene glycol Saudi CIF passed a median test on 1,103
    while carrying a reading of 6,755, and a median hides a spread.
    """
    rows = session.execute(
        select(MarketIdentityModel.product_family, MarketIdentityModel.market_geography,
               MarketIdentityModel.commercial_basis, MarketIdentityModel.currency,
               PriceObservationModel.price)
        .join(PriceObservationModel,
              PriceObservationModel.identity_id == MarketIdentityModel.id)
        .where(PriceObservationModel.price.isnot(None))).all()
    series: dict[tuple, list[float]] = {}
    for chemical, geography, basis, currency, price in rows:
        series.setdefault((chemical, currency, geography, basis), []).append(float(price))
    level: dict[tuple, list[float]] = {}
    for (chemical, currency, _g, _b), prices in series.items():
        level.setdefault((chemical, currency), []).extend(prices)
    level = {key: st.median(prices) for key, prices in level.items()}

    opened = []
    for (chemical, currency, geography, basis), prices in sorted(series.items()):
        middle = level[(chemical, currency)]
        if not middle:
            continue
        ratio = st.median(prices) / middle
        spread = (max(prices) / min(prices)) if min(prices) > 0 else 0.0
        off_level = ratio >= factor or ratio <= 1.0 / factor
        # A series whose own readings disagree by more than an order of magnitude is not a market
        # whatever its median says.
        off_spread = spread >= 10.0
        if not (off_level or off_spread):
            continue
        observed = (f"{chemical} {geography} {basis}: {len(prices)} readings, median "
                    f"{st.median(prices):,.0f} {currency} against {middle:,.0f} for this chemical "
                    f"({ratio:.1f}x), spread {spread:.1f}x")
        opened.append(store.open_case(session, m.Case(
            gap=m.MARKET, observed=observed, origin_kind="PRICE",
            chemical=chemical, geography=geography,
            data={"basis": basis, "currency": currency, "readings": len(prices),
                  "median": round(st.median(prices), 2), "chemical_level": round(middle, 2),
                  "ratio": round(ratio, 2), "spread": round(spread, 2)})))
    return opened


def work_the_free_enquiries(session: Session, case_id: str) -> int:
    """Run every pending enquiry that needs nothing but what we already hold.

    The roster and our own prices cost nothing and cannot be refused, so they run on every case
    without being asked for. Anything needing a fetch or a person stays PENDING, and pending is
    visible.
    """
    case = next((c for c in store.cases(session) if c.id == case_id), None)
    if case is None:
        raise KeyError(case_id)
    ran = 0
    for enquiry in store.enquiries(session, case_id, outcome=m.PENDING):
        if enquiry.capability == m.OUR_EVENTS:
            ran += _run_named_in_the_text(session, case, enquiry)
        elif enquiry.capability == m.ROSTER:
            ran += _run_roster(session, case, enquiry)
        elif enquiry.capability == m.OUR_PRICES:
            ran += _run_our_prices(session, case, enquiry)
    return ran


def _run_roster(session, case, enquiry) -> int:
    chemical, geography = case.chemical or "", case.geography or ""
    if not roster.have_a_map(chemical, geography):
        store.record(session, enquiry.id, outcome=m.UNANSWERABLE,
                     found=(f"no plant roster exists for {chemical or '?'} in "
                            f"{geography or '?'}. The map covers nine chemical-geography pairs of "
                            "about forty priced, and none of the feedstocks. This is a collection "
                            "requirement, not a dead end."))
        return 1
    found = roster.by_capacity(roster.candidates(chemical, geography, observed=case.observed))
    if not found:
        store.record(session, enquiry.id, outcome=m.EXHAUSTED,
                     found="the roster for this market records no plants")
        return 1
    store.add_candidates(session, case.id, [
        {k: v for k, v in one.items() if k in
         ("name", "owner", "chemical", "geography", "province", "capacity_kt", "from_source")}
        for one in found])
    region = roster.region_named(case.observed)
    named = ", ".join(f"{one['name']} ({one['capacity_kt']:,.0f} kt)" if one["capacity_kt"]
                      else one["name"] for one in found)
    store.record(session, enquiry.id,
                 outcome=m.NARROWED_BY if len(found) > 1 else m.ANSWERED,
                 found=(f"{len(found)} candidate(s)"
                        + (f" in {region} China" if region else "")
                        + f" from our own roster: {named}"),
                 data={"candidates": len(found), "region": region,
                       "total_kt": sum(one["capacity_kt"] or 0.0 for one in found)})
    return 1


def _run_our_prices(session, case, enquiry) -> int:
    chemical, geography = case.chemical or "", case.geography or ""
    if not chemical or not geography:
        store.record(session, enquiry.id, outcome=m.UNANSWERABLE,
                     found="the case does not yet name a chemical and a geography to look up")
        return 1
    when = case.observed_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    found = our_prices.trace(session, chemical, geography, when)
    if not found.get("held"):
        store.record(session, enquiry.id, outcome=m.UNANSWERABLE, found=found["why"], data=found)
        return 1
    if "move_pct" not in found:
        store.record(session, enquiry.id, outcome=m.EXHAUSTED, found=found["why"], data=found)
        return 1
    store.record(session, enquiry.id,
                 outcome=m.ANSWERED if found["unusual"] else m.EXHAUSTED,
                 found=("our own series moved " if found["unusual"] else
                        "our own series did not move unusually: ") + found["why"],
                 data=found)
    return 1


def assessments_misfiled(session: Session) -> list[dict]:
    """Events filed as producer actions whose own headline is a price bulletin or a roundup.

    Not cases - we know what these are. Reported so the classification defect has a number against
    it: 30 of the 59 PRODUCER_PRICE records held on 17 October 2026 were bulletins, and the weekly
    plant-movement censuses (国内MDI装置动态汇总 and its siblings) are multi-plant tables flattened
    to one sentence, which is the richest thing in the feed being destroyed at classification.
    """
    out = []
    for event in session.execute(select(EventRecordModel)).scalars().all():
        headline = _headline(event.data)
        if headline and looks_like_an_assessment(headline):
            out.append({"source_id": event.source_id, "event_type": event.event_type,
                        "material": event.material, "headline": headline,
                        "when": str(event.available_from)[:10]})
    return out


def _run_named_in_the_text(session, case, enquiry) -> int:
    """W1: is the actor sitting in the observation, unparsed?

    The signal lane holds 28 producer names in both scripts and still left `actor` empty on
    "[DMF]：聊城鲁西化工DMF价格上调", where 聊城鲁西化工 is Liaocheng Luxi Chemical. An observation
    whose actor is written in it is not an unattributed observation; it is an unread one, and that
    is the cheapest thing in the chain to find out.
    """
    from app.producer_signals.export import _PRODUCERS

    text = case.observed or ""
    hits = [name for token, name in _PRODUCERS if token and token in text]
    if not hits:
        store.record(session, enquiry.id, outcome=m.EXHAUSTED,
                     found="no producer we hold a name for appears in the observation itself")
        return 1
    found = sorted(set(hits))
    store.add_candidates(session, case.id,
                         [{"name": one, "owner": one, "chemical": case.chemical,
                           "geography": case.geography, "state": "CONFIRMED",
                           "why": "named in the observation itself",
                           "from_source": "producer_signals name list"} for one in found])
    store.record(session, enquiry.id, outcome=m.ANSWERED,
                 found=(f"named in the observation: {', '.join(found)}. The lane left the actor "
                        "empty while the name was in the headline."),
                 data={"named": found})
    if len(found) == 1:
        store.close(session, case.id, state=m.RESOLVED,
                    answer=(f"{found[0]}, read out of the publisher's own headline. The event was "
                            "classified unattributed because the actor was never parsed, not "
                            "because nobody said who it was."))
    return 1
