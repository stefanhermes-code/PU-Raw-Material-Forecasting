"""Step 6 -> producer-signal feed.

Charlie's application contract (`src/raw_material_forecasting/producer_signals.py`, v0.4.58) reads a
JSON array from `data/producer_signals.json` and shows a row to customers only when
`verification_status == "VERIFIED"`. His renderer notes "Acquisition remains owned by the Step 6
source-expansion work" — this module is that acquisition.

Every candidate is decided automatically (see `verification.py`) and there is no review queue: a
signal is either published with an honest evidence tier, or rejected as unattributable. Decisions are
recomputed on every export, so a report that arrives unattributed is published by itself the moment a
second independent publisher carries the same event - nobody has to notice it happen.

Rejected rows are written to a sidecar audit file rather than silently discarded, so the engine can be
held to account for what it chose not to show.

Nothing is invented. The publisher's own headline is carried verbatim in `what_changed`; the English
prefix states only what the category already asserts. Chinese market reports routinely anonymise the
plant ("西北某TDI工厂" = an unnamed Northwest TDI plant) and that is reported as such, never guessed.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import EventRecordModel
from app.sources.connectors.mysteel_pu import _material_of
from app.producer_signals.naming import has_chinese, render_what_changed, romanise
from app.producer_signals.relevance import relationship
from app.producer_signals.verification import VERIFIED, collapse_to_events, verify

# Named producers as they appear in the headlines. Anything not matched stays anonymous.
_PRODUCERS: tuple[tuple[str, str], ...] = (
    ("万华化学", "Wanhua Chemical"),
    # BorsodChem is Wanhua: Wanhua Chemical has owned it since 2011, and a European buyer reading a
    # BorsodChem letter is reading a Wanhua decision. Placed above the Latin "Wanhua" entry below so
    # a line naming both resolves to the European plant rather than to the group. Without this the
    # name is unknown to the roster and the notice is dropped as unattributable.
    ("BorsodChem", "Wanhua Chemical (BorsodChem)"),
    # The list held only the Chinese rendering of Huntsman, which no European notice uses.
    ("Huntsman", "Huntsman"),
    ("Stepan", "Stepan"),
    ("聊城鲁西化工", "Luxi Chemical"),
    ("山东华鲁恒升", "Hualu Hengsheng"),
    ("江苏华昌化工", "Huachang Chemical"),
    ("山东金岭", "Shandong Jinling"),
    ("百川股份", "Baichuan"),
    ("沾化瑜凯", "Zhanhua Yukai"),
    ("宁夏中石化长城", "Sinopec Great Wall Ningxia"),
    ("河南开祥", "Henan Kaixiang"),
    ("南通星辰", "Nantong Xingchen"),
    ("福建海泉", "Fujian Haiquan"),
    ("沧州大化", "Cangzhou Dahua"),
    ("甘肃银光", "Gansu Yinguang"),
    ("科思创", "Covestro"),
    ("巴斯夫", "BASF"),
    ("中石化", "Sinopec"),
    ("中石油", "PetroChina"),
    ("陶氏", "Dow"),
    ("亨斯迈", "Huntsman"),
    ("东曹", "Tosoh"),
    ("锦湖三井", "Kumho Mitsui"),
    ("韩华", "Hanwha"),
    ("BASF", "BASF"),
    ("Covestro", "Covestro"),
    ("Wanhua", "Wanhua Chemical"),
)

# A hand-kept list can only ever name the producers someone remembered. The live feed showed real,
# checkable announcements from 隆华新材, 联泓格润 and 广西氯碱 being filed as "Unnamed producer" purely
# because they were missing from the list above - and an unnamed signal is one we throw away, so the
# gap was silently destroying good intelligence. Chinese corporate names are structurally regular, so
# they are also detected by shape and carried verbatim: a name we cannot translate is still a name.
_CORPORATE_NAME_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}(?:化学|化工|石化|氯碱|新材料|新材|股份|集团|实业|能源|科技|工贸|化纤|大化)")

# Words that look corporate but name an industry, a region or a report, not a company.
_NOT_A_COMPANY = ("行业", "市场", "中国", "全球", "国内", "进口", "出口", "地区", "东南亚", "欧洲")

# Longzhong headlines are formatted "[product]：<actor><product><action>" or, for industry news,
# "[行业快讯]：<actor><project><outcome>". Some real producers carry no corporate suffix at all
# (山东信发, 联泓格润), so the actor is also recovered positionally from that structure.
_BRACKETED_RE = re.compile(r"^\[(?P<tag>[^\]]+)\][:：]\s*(?P<rest>.+)$")
_ACTION_WORDS = re.compile(r"(价格|最新|快讯|快报|复报|上调|下调|竞价|结算|挂牌|项目|装置|工厂|停车|检修|开车)")
_DESCRIPTORS = ("一体化", "新建", "扩建", "改扩建", "股份有限公司", "有限公司", "公司")
_CJK_RUN_RE = re.compile(r"^[\u4e00-\u9fff]{2,10}")


def _actor_from_structure(headline: str) -> str:
    """Actor recovered from the publisher's headline layout, for names with no corporate suffix."""
    match = _BRACKETED_RE.match(headline.strip())
    if not match:
        return ""
    rest = match.group("rest")
    tag = match.group("tag")
    if "快讯" not in tag and "快报" not in tag:
        rest = rest.replace(tag, "")           # the product name is not part of the actor
    cut = _ACTION_WORDS.search(rest)
    candidate = (rest[: cut.start()] if cut else rest).strip()
    run = _CJK_RUN_RE.match(candidate)
    if not run:
        return ""                              # starts with a date or a figure: a market report
    candidate = run.group(0)
    for suffix in _DESCRIPTORS:
        if candidate.endswith(suffix) and len(candidate) > len(suffix) + 1:
            candidate = candidate[: -len(suffix)]
    if len(candidate) < 2 or any(word in candidate for word in _NOT_A_COMPANY):
        return ""
    return candidate

# Region words used when the plant itself is not named.
_REGIONS: tuple[tuple[str, str], ...] = (
    ("华东", "East China"), ("华北", "North China"), ("华南", "South China"),
    ("西北", "Northwest China"), ("西部", "Western China"), ("北方", "Northern China"),
    ("上海", "Shanghai"), ("山东", "Shandong"), ("宁夏", "Ningxia"),
    # SunSirs writes in English; the same regions, the same meaning.
    ("East China", "East China"), ("North China", "North China"), ("South China", "South China"),
    ("Northwest China", "Northwest China"), ("Shandong", "Shandong"), ("Ningxia", "Ningxia"),
    ("Shanghai", "Shanghai"),
)

# English corporate names, for the same reason the Chinese ones are detected by shape: a list can
# only name the producers someone remembered.
_ENGLISH_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\s+"
    r"(Chemical|Chemicals|Petrochemical|Polyurethanes?|Industries|Materials|Group|Corporation)\b")
_ENGLISH_NOT_A_COMPANY = ("China", "Asia", "Europe", "Domestic", "Global", "Chinese", "The", "A", "An")

# Ordered: first match wins. Categories must exist in CUSTOMER_VISIBLE_CATEGORIES.
_CATEGORY_RULES: tuple[tuple[str, str, str], ...] = (
    (r"不可抗力|force majeure", "FORCE_MAJEURE", "Force majeure reported"),
    (r"故障|事故|\b(explosion|fire|accident|breakdown)\b", "INCIDENT", "Plant incident reported"),
    (r"停车|\b(shut ?down|halt(?:ed|s)?|outage|off-?line|suspend(?:ed|s)?|curtail(?:ed|ment)?)\b",
     "SHUTDOWN", "Plant shutdown reported"),
    (r"检修|\b(maintenance|overhaul|turnaround)\b", "PLANNED_MAINTENANCE", "Plant maintenance reported"),
    (r"开车|复产|重启|\b(restart(?:ed|s)?|resum(?:ed|es|ption)|back on ?line|start-?up|commission(?:ed|ing)?)\b",
     "PLANT_STARTUP", "Plant restart reported"),
    (r"上调|抬涨|提涨|\b(rais(?:ed|es)|hik(?:ed|es)|lift(?:ed|s)|increase[ds]?)\b.{0,24}\bprice"
     r"|\bprice (?:increase|hike|rise)\b",
     "PRICE_INCREASE", "Producer price increase reported"),
    (r"下调|下跌|回落|\b(lower(?:ed|s)|cut|reduc(?:ed|es))\b.{0,24}\bprice|\bprice (?:cut|reduction)\b",
     "PRICE_DECREASE", "Producer price decrease reported"),
    (r"结算价|结挂价|挂牌|价格快报|价格快讯|价格复报|竞价"
     r"|\b(list(?:ed)? price|settlement price|quotation|offer price)\b",
     "PRICE_ACTION", "Producer price action reported"),
    # Capacity and corporate direction: what goes on in the market - capacity, a company growing or
    # shrinking, new ventures. Until these rules existed every such headline the engine collected was
    # dropped here, because a row with no category is skipped, while EVENT_CLASS already carried
    # CAPACITY entries nothing could produce. Exit is tested before venture, because "exit from a
    # joint venture" names a venture and would otherwise be filed as one being formed.
    (r"投资|增资扩产|\binvest(?:s|ed|ing|ment)?\b.{0,60}\b(?:plant|unit|line|train|facility|capacity|project)\b"
     r"|\b(?:plant|unit|line|train|facility|project)\b.{0,40}\binvestment\b",
     "CAPACITY_INVESTMENT", "Producer investment in capacity reported"),
    (r"退出|剥离|转让股权|出售股权|关停|永久关闭|永久停产"
     r"|\bexit(?:s|ed|ing)?\b.{0,40}\b(?:venture|business|market|plant|site)"
     r"|\bdivest(?:s|ed|ment|iture)?\b"
     r"|\bsell(?:s|ing)?\b.{0,30}\b(?:stake|business|plant|site)"
     r"|\b(?:permanent(?:ly)? )?clos(?:e|es|ed|ure|ing)\b.{0,30}\b(?:plant|unit|line|site|facility)\b",
     "CORPORATE_EXIT", "Corporate exit reported"),
    (r"合资|合作协议|战略合作|战略投资|收购|并购|入股|增资|参股"
     r"|\b(joint venture|strategic (?:partner(?:ship)?|investor)|partner(?:s|ed|ship) with|"
     r"acquir(?:e|es|ed|ing)|acquisition|merger|takeover|stake in|capital increase)\b",
     "CORPORATE_VENTURE", "Corporate venture reported"),
    (r"新建|扩建|扩产|投产|新增产能|产能扩张|装置投料"
     r"|\bnew (?:plant|unit|line|train|capacity|facility)\b|\bexpansion\b|\bexpand(?:s|ed|ing)? capacity\b"
     r"|\bdebottleneck(?:ing)?\b|\bworld[- ]scale\b|\bgreenfield\b|\bbrownfield\b"
     r"|\b(?:plans?|planned|to build|building|construct(?:s|ed|ing|ion)?)\b.{0,40}\b(?:plant|unit|line|train)\b",
     "CAPACITY_ADDITION", "Capacity addition reported"),
)


def _actor_of(headline: str) -> tuple[str, str, bool]:
    """(display name, actor key for matching, whether a real producer was named).

    A named producer makes the claim attributable; a bare region does not, and the verification tier
    depends on that distinction, so it is decided here where the headline is still in hand.
    """
    for token, name in _PRODUCERS:
        if token in headline:
            return name, name, True
    match = _CORPORATE_NAME_RE.search(headline)
    if match and not any(word in match.group(0) for word in _NOT_A_COMPANY):
        name = romanise(match.group(0))
        if name:
            return name, name, True
    structural = _actor_from_structure(headline)
    if structural:
        name = romanise(structural)
        # No Latin rendering means no name we can show: an English screen must not carry Chinese, and
        # a producer we cannot name is treated as unnamed rather than printed in the wrong script.
        if name:
            return name, name, True
    english = _ENGLISH_NAME_RE.search(headline)
    if english and english.group(1) not in _ENGLISH_NOT_A_COMPANY:
        name = f"{english.group(1)} {english.group(2)}"
        return name, name, True
    for token, region in _REGIONS:
        if token in headline:
            return f"Unnamed producer ({region})", f"REGION:{region}", False
    return "Unnamed producer", "", False


def _category_of(headline: str) -> tuple[str, str] | None:
    for pattern, category, label in _CATEGORY_RULES:
        if re.search(pattern, headline, re.I):
            return category, label
    return None


def _signal_id(event: EventRecordModel) -> str:
    return f"step6-{event.id}"


def build_candidates(session) -> list[dict]:
    """Engine event rows rendered in the application's producer-signal schema, undecided."""
    out: list[dict] = []
    for event in session.scalars(select(EventRecordModel).order_by(EventRecordModel.available_from)):
        data = event.data or {}
        headline = str(data.get("headline") or "").strip()
        if not headline:
            continue
        classified = _category_of(headline)
        if classified is None:
            continue
        category, label = classified
        announced = event.available_from
        announced_date = (announced.date() if isinstance(announced, datetime) else announced)
        display, actor, named = _actor_of(headline)
        # Re-read the material from the headline when the stored row has none. Product coverage grows,
        # and an already-collected event would otherwise stay blank until it happened to be re-fetched
        # - which is why four live rows read "chemical plant" with an empty Chemical column.
        material = data.get("material") or _material_of(headline) or ""
        tier, chain_relevance = relationship(material)
        out.append({
            "signal_id": _signal_id(event),
            "source_id": event.source_id,
            "producer": display,
            "actor": actor,
            "producer_named": named,
            "chemical": material,
            "chemical_relationship": tier,
            "chemical_relevance": chain_relevance,
            # NOT defaulted to China. Every publisher the engine used to read wrote about the China
            # market, so a blank meant China and the default was harmless. ECHEMI reports globally:
            # with the default in place a Dow/Aramco joint venture in Saudi Arabia was printed on a
            # customer screen as a China signal. A geography nobody stated is left unstated.
            "geography": data.get("market_geography") or "",
            "category": category,
            # An English sentence composed from what was parsed - who, which product, what action,
            # what capacity. The headline is NOT machine-translated: that would put words in the
            # publisher's mouth. The original is kept in `source_headline` for traceability; it is
            # not part of Charlie's ProducerSignal schema, so it never reaches the screen.
            "what_changed": render_what_changed(
                category=category, label=label, actor=display, named=named,
                material=material, headline=headline),
            "source_headline": headline,
            "announced_at": announced_date.isoformat() if announced_date else None,
            "effective_from": None,
            "effective_to": None,
            "status": str(event.state or "ANNOUNCED"),
            "market_relevance": "",
            "verification_status": "",             # decided by verification.verify()
            "source_url": str(data.get("url") or ""),
            "source_date": announced_date.isoformat() if announced_date else None,
        })
    return out


def default_feed_path() -> Path:
    """`PRODUCER_SIGNALS_FEED` if set, else the application's data/producer_signals.json.

    The engine CLI runs with cwd=market_data_engine while the Streamlit app runs from the project
    root, so a bare relative path would resolve to two different files.
    """
    configured = os.environ.get("PRODUCER_SIGNALS_FEED")
    if configured:
        return Path(configured)
    # app/producer_signals/export.py -> parents[3] is the project root that holds both
    # market_data_engine/ and the Streamlit app.
    return Path(__file__).resolve().parents[3] / "data" / "producer_signals.json"


def rejected_feed_path(path: Path) -> Path:
    """Audit sidecar next to the feed: what the engine decided not to publish, and why."""
    return path.with_name(path.stem + "_rejected.json")


def export_producer_signals(session_factory, path: Path | None = None) -> dict:
    """Rewrite the feed from a fresh automatic decision on every signal.

    There is no merge-and-preserve step any more. Preserving an old decision only made sense while a
    person was expected to make it; now the decision is a function of the evidence, and the evidence
    grows. Re-deciding every run is what lets a signal get corroborated - and published - on its own.
    """
    path = Path(path or default_feed_path())
    with session_factory() as session:
        # Verify first, so every report contributes to the corroboration count, then collapse - a
        # collapsed event must still know it was carried by two publishers.
        decided = collapse_to_events(verify(build_candidates(session), session))

    published = [row for row in decided if str(row.get("verification_status")).upper() == VERIFIED]
    rejected = [row for row in decided if str(row.get("verification_status")).upper() != VERIFIED]

    def _order(row: dict) -> tuple:
        return (str(row.get("announced_at") or ""), str(row.get("signal_id") or ""))

    published.sort(key=_order, reverse=True)
    rejected.sort(key=_order, reverse=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(published, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rejected_feed_path(path).write_text(
        json.dumps(rejected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    tiers: dict[str, int] = {}
    for row in decided:
        tier = str(row.get("evidence_tier") or "UNKNOWN")
        tiers[tier] = tiers.get(tier, 0) + 1

    return {
        "path": str(path),
        "rejected_path": str(rejected_feed_path(path)),
        "total": len(published),
        "published": len(published),
        "rejected": len(rejected),
        "by_tier": tiers,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
