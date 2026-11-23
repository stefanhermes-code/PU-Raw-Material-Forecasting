"""Automatic verification of market-intelligence signals.

A signal is a *report of an event* - a plant going down, a producer moving its price. Verifying it
means asking whether the report is trustworthy enough to put in front of a customer. That question is
answered from the report's own provenance, never from a human queue: a review queue that nobody works
is just a place where intelligence goes to expire.

So there is no pending state. Every signal is decided on every export, and only two outcomes exist:

  VERIFIED   published, carrying an honest evidence tier and confidence label
  REJECTED   dropped - not shown, not queued, with the reason recorded for the engine's own audit

The evidence tiers, strongest first:

  PRODUCER_DIRECT      the publisher IS the actor (own press release / own listed-price page). The
                       report and the event have the same author; there is nothing to corroborate.
  CORROBORATED         two *independent publishers* report the same event - same event class, same
                       material, same actor - within the window. Independence is by lineage, not by
                       source_id: Mysteel IS Longzhong, and SunSirs and 100ppi are one ecosystem, so
                       neither pair can corroborate itself.
  NAMED_SINGLE_SOURCE  one publisher, but the actor is named. The claim is attributable and falsifiable
                       - "Wanhua's Yantai line is down" can be checked and the publisher has staked its
                       name on it. Published as single-source.
  UNATTRIBUTED         one publisher, no named actor ("西北某TDI工厂" = a certain Northwest TDI plant).
                       Nobody can check it and nobody is accountable for it. Rejected.

Re-decided every run, so a signal that arrives unattributed today is published the moment a second
independent publisher reports the same event, with no one having to notice.

PRICE MOVEMENT IS NOT VERIFICATION. Whether the market moved afterwards says nothing about whether the
report was true, and waiting for it destroys the only thing a signal is for - warning before the move.
It is carried separately as `price_check`, purely as context, and it never gates publication. A price
announcement the market then contradicts stays published, and says so.

Defaults below are business judgement, not physics; they are meant to be tuned.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceObservationModel

# Publishers that ARE the actor they report on: the report and the event have the same author, so
# there is nothing to corroborate. Neither producer publishes prices - Wanhua never has and Covestro
# stopped issuing public price-increase announcements (checked 2026-09-09) - so what lands here is
# capacity, investment and ownership, which is the half of the market the price desks report late.
# The exchange filings are the same argument put more strongly: a listed company files under legal
# obligation, on the day, under its own name.
PRODUCER_DIRECT_SOURCES: frozenset[str] = frozenset({
    "covestro_press",
    "stepan_press",
    "wanhua_news",
    "sse_600230_filings",
    "sse_600309_filings",
    "sse_600426_filings",
})

# Publisher lineage. Sources sharing a value are NOT independent of each other.
PUBLISHER_LINEAGE: dict[str, str] = {
    "longzhong_tdi": "MYSTEEL_LONGZHONG",
    "longzhong_pmdi": "MYSTEEL_LONGZHONG",
    "longzhong_polyol": "MYSTEEL_LONGZHONG",
    "longzhong_polyester": "MYSTEEL_LONGZHONG",
    "mysteel_pu": "MYSTEEL_LONGZHONG",
    "sunsirs_tdi": "SUNSIRS_100PPI",
    "100ppi_polyol_mw3000": "SUNSIRS_100PPI",
    "echemi_tdi": "ECHEMI",
    "echemi_pmdi": "ECHEMI",
    "echemi_polyol": "ECHEMI",
    "echemi_enterprise": "ECHEMI",
    "echemi_news": "ECHEMI",
    # Each producer and each issuer is its own lineage: two producers reporting the same event are
    # genuinely two publishers, and an issuer's filing is independent of the trade press.
    "covestro_press": "COVESTRO",
    "stepan_press": "STEPAN",
    "wanhua_news": "WANHUA",
    "sse_600230_filings": "SSE_600230",
    "sse_600309_filings": "SSE_600309",
    "sse_600426_filings": "SSE_600426",
    "sci99_tdi": "SCI99",
    "chemanalyst_tdi": "CHEMANALYST",
    "businessanalytiq_tdi": "BUSINESSANALYTIQ",
    "imarc_tdi": "IMARC",
    "imarc_mdi": "IMARC",
    "procurementresource_polyether": "PROCUREMENT_RESOURCE",
}

# Categories collapsed to the event they describe, so publishers using different words still match.
# Direction is preserved: an increase must never corroborate a decrease.
EVENT_CLASS: dict[str, str] = {
    "FORCE_MAJEURE": "SUPPLY_LOSS",
    "INCIDENT": "SUPPLY_LOSS",
    "SHUTDOWN": "SUPPLY_LOSS",
    "CURTAILMENT": "SUPPLY_LOSS",
    "PLANNED_MAINTENANCE": "SUPPLY_LOSS",
    "PERMANENT_CLOSURE": "SUPPLY_LOSS",
    "STRUCTURAL_REDUCTION": "SUPPLY_LOSS",
    "PLANT_STARTUP": "SUPPLY_RETURN",
    "CAPACITY_ADDITION": "CAPACITY",
    "CAPACITY_EXPANSION": "CAPACITY",
    "CAPACITY_INVESTMENT": "CAPACITY",
    "DEBOTTLENECK": "CAPACITY",
    # A venture and an exit are left in classes of their own on purpose: one publisher reporting a
    # joint venture must never be taken as corroborating another reporting a withdrawal.
    "PRICE_INCREASE": "PRICE_UP",
    "PRICE_DECREASE": "PRICE_DOWN",
    "PRICE_ACTION": "PRICE_ANY",       # publisher reports a price move without stating direction
}

VERIFIED = "VERIFIED"
REJECTED = "REJECTED"

TIER_PRODUCER_DIRECT = "PRODUCER_DIRECT"
TIER_CORROBORATED = "CORROBORATED"
TIER_NAMED_SINGLE = "NAMED_SINGLE_SOURCE"
TIER_UNATTRIBUTED = "UNATTRIBUTED"

CONFIDENCE = {
    TIER_PRODUCER_DIRECT: "CONFIRMED",
    TIER_CORROBORATED: "CORROBORATED",
    TIER_NAMED_SINGLE: "SINGLE_SOURCE",
    TIER_UNATTRIBUTED: "UNSUPPORTED",
}

# What a publisher is called on screen. `market_relevance` is shown to customers, so it says
# "Reported by Longzhong and SunSirs", never "MYSTEEL_LONGZHONG" or a source_id.
PUBLISHER_NAMES: dict[str, str] = {
    "MYSTEEL_LONGZHONG": "Longzhong",
    "SUNSIRS_100PPI": "SunSirs",
    "ECHEMI": "ECHEMI",
    "SCI99": "SCI99",
    "CHEMANALYST": "ChemAnalyst",
    "BUSINESSANALYTIQ": "Business Analytiq",
    "COVESTRO": "Covestro",
    "WANHUA": "Wanhua Chemical",
    "SSE_600230": "Cangzhou Dahua (exchange filing)",
    "SSE_600309": "Wanhua Chemical (exchange filing)",
    "SSE_600426": "Hualu Hengsheng (exchange filing)",
}

CORROBORATION_WINDOW_DAYS = 3      # two publishers this far apart are still reporting the same event
PRICE_WINDOW_DAYS = 5              # how long we keep looking for a market reaction (context only)
PRICE_MOVE_THRESHOLD = 0.003       # 0.3% - above day-to-day assessment noise between publishers


def lineage_of(source_id: str) -> str:
    return PUBLISHER_LINEAGE.get(source_id, source_id)


def publisher_name(lineage: str) -> str:
    return PUBLISHER_NAMES.get(lineage, lineage.replace("_", " ").title())


def _name_list(lineages) -> str:
    names = sorted(publisher_name(lineage) for lineage in lineages)
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def event_class_of(category: str) -> str:
    return EVENT_CLASS.get(str(category or "").upper(), str(category or "").upper())


def _classes_agree(left: str, right: str) -> bool:
    """Same event class - with PRICE_ANY matching either direction, since it asserts neither."""
    if left == right:
        return True
    price_any = {"PRICE_ANY"}
    directional = {"PRICE_UP", "PRICE_DOWN"}
    return ({left, right} & price_any) and ({left, right} & directional)


# Corporate furniture. Two publishers rarely write a company's name the same way - Longzhong files
# 聊城鲁西化工 and SunSirs files "Luxi Chemical Group" - and if the two keys differ the event is
# counted twice instead of once, which is precisely the corroboration we are trying to detect. The
# suffix is stripped for matching only; what is published stays as the publisher wrote it.
_CORPORATE_SUFFIXES: tuple[str, ...] = (
    "PETROCHEMICAL", "CHEMICALS", "CHEMICAL", "POLYURETHANES", "POLYURETHANE", "INDUSTRIES",
    "CORPORATION", "MATERIALS", "GROUP", "CHEM", "CORP", "CO", "LTD", "INC", "SA", "AG",
)


def canonical_actor(name: str) -> str:
    """A stable key for one company across publishers and languages."""
    key = " ".join(str(name or "").upper().replace(".", " ").replace(",", " ").split())
    if not key or key.startswith("REGION:"):
        return key
    changed = True
    while changed:
        changed = False
        for suffix in _CORPORATE_SUFFIXES:
            if key.endswith(" " + suffix) and len(key) - len(suffix) - 1 >= 3:
                key = key[: -len(suffix) - 1].strip()
                changed = True
    return key


def _actor_key(row: dict) -> str:
    """Who the event is about. Named producer if known, else the region the publisher gave."""
    actor = str(row.get("actor") or "").strip()
    if not actor:
        actor = str(row.get("producer") or "").strip()
    if actor.upper().startswith("REGION:"):
        return actor.upper()
    return canonical_actor(actor)


def _as_date(value) -> date | None:
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


def _price_series(session, product_family: str) -> list[tuple[date, float]]:
    """Governed daily reference series for a product: mean across publishers per observation date."""
    rows = session.execute(
        select(PriceObservationModel.observation_date, PriceObservationModel.price)
        .join(MarketIdentityModel, PriceObservationModel.identity_id == MarketIdentityModel.id)
        .where(
            MarketIdentityModel.product_family == product_family,
            MarketIdentityModel.commercial_basis == "EXW",
            MarketIdentityModel.market_geography == "China",
        )
    ).all()
    by_day: dict[date, list[float]] = {}
    for observed, price in rows:
        day = _as_date(observed)
        if day is not None and price is not None:
            by_day.setdefault(day, []).append(float(price))
    return sorted((day, sum(values) / len(values)) for day, values in by_day.items())


def _price_move(series: list[tuple[date, float]], announced: date, window: int):
    """Relative move from the last reading on/before `announced` to the largest move within `window`."""
    before = [(day, value) for day, value in series if day <= announced]
    after = [(day, value) for day, value in series if announced < day <= announced + timedelta(days=window)]
    if not before or not after:
        return None
    base_day, base = before[-1]
    end_day, end = max(after, key=lambda item: abs(item[1] - base))
    if base == 0:
        return None
    return (end - base) / base, base_day, end_day


def _price_context(row: dict, session, cache: dict) -> tuple[str, str]:
    """(price_check, sentence). Context only - this never decides whether a signal is published."""
    klass = event_class_of(row.get("category"))
    if klass not in {"PRICE_UP", "PRICE_DOWN", "PRICE_ANY"}:
        return "NOT_APPLICABLE", ""
    material = str(row.get("chemical") or "")
    announced = _as_date(row.get("announced_at"))
    if not material or announced is None:
        return "NO_DATA", ""
    if material not in cache:
        cache[material] = _price_series(session, material)
    move = _price_move(cache[material], announced, PRICE_WINDOW_DAYS)
    if move is None:
        return "NO_DATA", ""
    change, base_day, end_day = move
    span = f"between {base_day.isoformat()} and {end_day.isoformat()}"
    if abs(change) < PRICE_MOVE_THRESHOLD:
        return "NO_MOVE", f"China EXW {material} has not moved since ({change:+.1%} {span})."
    followed = change > 0 if klass == "PRICE_UP" else change < 0 if klass == "PRICE_DOWN" else True
    if followed:
        return "CONSISTENT", f"China EXW {material} then moved {change:+.1%} {span}."
    return "CONTRADICTED", f"The market went the other way: China EXW {material} moved {change:+.1%} {span}."


def _corroborators(row: dict, candidates: list[dict]) -> set[str]:
    """Independent publisher lineages reporting this same event within the window (incl. this one)."""
    announced = _as_date(row.get("announced_at"))
    if announced is None:
        return set()
    klass = event_class_of(row.get("category"))
    material = str(row.get("chemical") or "").upper()
    actor = _actor_key(row)
    found = {lineage_of(str(row.get("source_id") or ""))}
    for other in candidates:
        if other is row:
            continue
        other_day = _as_date(other.get("announced_at"))
        if other_day is None or abs((other_day - announced).days) > CORROBORATION_WINDOW_DAYS:
            continue
        if str(other.get("chemical") or "").upper() != material:
            continue
        if _actor_key(other) != actor:
            continue
        if not _classes_agree(klass, event_class_of(other.get("category"))):
            continue
        found.add(lineage_of(str(other.get("source_id") or "")))
    return found


def _tier(row: dict, candidates: list[dict]) -> tuple[str, set[str]]:
    if str(row.get("source_id") or "") in PRODUCER_DIRECT_SOURCES:
        return TIER_PRODUCER_DIRECT, {lineage_of(str(row.get("source_id") or ""))}
    lineages = _corroborators(row, candidates)
    if len(lineages) >= 2:
        return TIER_CORROBORATED, lineages
    if bool(row.get("producer_named")):
        return TIER_NAMED_SINGLE, lineages
    return TIER_UNATTRIBUTED, lineages


def verify(candidates: list[dict], session) -> list[dict]:
    """Decide every candidate. Pure: no writes. Rejected rows are returned, flagged, not dropped here."""
    cache: dict[str, list[tuple[date, float]]] = {}
    decided: list[dict] = []

    for row in candidates:
        out = dict(row)
        tier, lineages = _tier(row, candidates)
        price_check, price_sentence = _price_context(row, session, cache)

        if tier == TIER_PRODUCER_DIRECT:
            basis = "Announced by the producer."
        elif tier == TIER_CORROBORATED:
            basis = f"Reported independently by {_name_list(lineages)}."
        elif tier == TIER_NAMED_SINGLE:
            basis = f"Reported by {publisher_name(lineage_of(str(row.get('source_id') or '')))}, one source so far."
        else:
            basis = "Not published: no plant or producer is named and no second publisher reported it."

        out["verification_status"] = REJECTED if tier == TIER_UNATTRIBUTED else VERIFIED
        out["evidence_tier"] = tier
        out["confidence"] = CONFIDENCE[tier]
        out["corroborating_publishers"] = sorted(lineages)
        out["price_check"] = price_check

        # Two different readers, two different fields.
        #
        # `market_relevance` is the customer's column: why this chemical matters to the market they
        # are looking at, and what the market has done since. Which publisher carried the story is
        # our supply chain, not theirs - naming Longzhong on a customer screen tells them nothing they
        # can act on and quietly advertises where our intelligence comes from.
        #
        # `evidence_note` carries the provenance and stays in the internal view.
        chain = str(row.get("chemical_relevance") or "").strip()
        chain = f"{chain.rstrip('.')}." if chain else ""
        out["market_relevance"] = " ".join(part for part in (chain, price_sentence) if part).strip()
        out["evidence_note"] = basis
        decided.append(out)

    return decided

# --- one event, one row -------------------------------------------------------------------------

# Rank for choosing which report represents a collapsed event.
_TIER_RANK = {TIER_PRODUCER_DIRECT: 3, TIER_CORROBORATED: 2, TIER_NAMED_SINGLE: 1, TIER_UNATTRIBUTED: 0}


def _same_event(left: dict, right: dict) -> bool:
    """Two reports of one event: same actor, same event class, same material, close enough in time.

    The time test differs by publisher on purpose. One publisher filing the same actor/class/material
    twice in a day is describing one event twice - Longzhong runs "DMF价格上调" and "DMF竞价上调" as
    separate headlines for a single price move, and rendered in English they are word for word the
    same row. Two *different* publishers are two witnesses to one event and may be a few days apart.
    """
    if _actor_key(left) != _actor_key(right):
        return False
    if str(left.get("chemical") or "").upper() != str(right.get("chemical") or "").upper():
        return False
    if not _classes_agree(event_class_of(left.get("category")), event_class_of(right.get("category"))):
        return False
    left_day, right_day = _as_date(left.get("announced_at")), _as_date(right.get("announced_at"))
    if left_day is None or right_day is None:
        return False
    same_publisher = lineage_of(str(left.get("source_id") or "")) == lineage_of(str(right.get("source_id") or ""))
    limit = 0 if same_publisher else CORROBORATION_WINDOW_DAYS
    return abs((left_day - right_day).days) <= limit


def collapse_to_events(decided: list[dict]) -> list[dict]:
    """One row per event, not one row per report.

    An unnamed actor is NOT an identity: two anonymous East China shutdowns are two events until a
    publisher says otherwise, so only named actors are ever collapsed. The surviving row keeps the
    earliest date - the first report is the signal - and lists every publisher that carried it.
    """
    groups: list[list[dict]] = []
    for row in sorted(decided, key=lambda r: (str(r.get("announced_at") or ""), str(r.get("signal_id") or ""))):
        if not row.get("producer_named"):
            groups.append([row])
            continue
        for group in groups:
            if group[0].get("producer_named") and _same_event(group[0], row):
                group.append(row)
                break
        else:
            groups.append([row])

    out: list[dict] = []
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        best = max(group, key=lambda r: (
            _TIER_RANK.get(str(r.get("evidence_tier")), 0),
            len(str(r.get("what_changed") or "")),
        ))
        merged = dict(best)
        merged["announced_at"] = min(str(r.get("announced_at") or "") for r in group if r.get("announced_at"))
        merged["merged_signal_ids"] = sorted(str(r.get("signal_id")) for r in group)
        merged["report_count"] = len(group)
        publishers = set()
        for row in group:
            publishers.update(row.get("corroborating_publishers") or [])
            publishers.add(lineage_of(str(row.get("source_id") or "")))
        merged["corroborating_publishers"] = sorted(publishers)
        out.append(merged)
    return out
