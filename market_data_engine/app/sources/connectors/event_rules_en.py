"""The English event vocabulary, held once so two publishers cannot drift apart.

SunSirs and ECHEMI both publish in English and both feed the same event space. When each carried its
own copy of the rules, a headline shape added for one publisher was silently invisible to the other,
which is the quiet way corroboration stops working: the same event reported twice is only ever
matched if both parsers recognise it as the same class.

The vocabulary covers what a company that buys TDI needs told about its market, which is wider than
outages and prices. Capacity coming and going, a producer putting money into a line, a joint venture
formed or abandoned - these change what is available to buy and from whom, on a horizon longer than a
price move. Each rule states the event class, the record type the engine stores, and the pattern.

Order matters: the first match wins, and the list runs from the most specific claim to the least, so
"force majeure" is never merely a shutdown and an investment in a new line is never merely a price.
"""
from __future__ import annotations

import re

# (kind, record_type, pattern). record_type is what lands in data["type"] and therefore what
# app/classification/classifier.py routes on - every value here must be known to that classifier.
EVENT_RULES: tuple[tuple[str, str, str], ...] = (
    ("PLANT_EVENT", "force_majeure", r"\bforce majeure\b"),
    ("PLANT_EVENT", "outage",
     r"\b(shut ?down|shutdown|shut|halt(?:ed|s)?|outage|unplanned|breakdown|explosion|fire|"
     r"maintenance|overhaul|turnaround|off-?line|offline|suspend(?:ed|s)?|curtail(?:ed|ment)?)\b"),
    ("PLANT_EVENT", "restart",
     # "starts up" as two words is how an English desk usually writes it and was being missed.
     r"\b(restart(?:ed|s|ing)?|resum(?:ed|es|ption)|back on ?line|start-?up|"
     r"start(?:s|ed|ing)? up|came? back online|commission(?:ed|ing)?)\b"),
    ("PRODUCER_PRICE", "producer_price_announcement",
     r"\b(list(?:ed)? price|settlement price|quotation|offer price|price (?:increase|hike|rise|cut|reduction))\b"
     r"|\b(?:rais(?:ed|es)|hik(?:ed|es)|lift(?:ed|s)|lower(?:ed|s)|cut|reduc(?:ed|es))\b.{0,24}\bprice"),
    # Corporate direction. Ordered before the generic capacity rule so "plans to invest in a new MDI
    # train" is recorded as the investment it is, with the capacity it names carried in the headline.
    ("CORPORATE", "capacity_investment",
     r"\b(invest(?:s|ed|ing|ment)?|fund(?:s|ed|ing)?)\b.{0,60}\b(plant|unit|line|train|facility|capacity|project)\b"
     r"|\b(plant|unit|line|train|facility|capacity|project)\b.{0,40}\binvest(?:ment|s|ed)?\b"),
    # Exit before venture: "exit from a joint venture" names a venture and would
    # otherwise be recorded as one being formed.
    ("CORPORATE", "corporate_exit",
     r"\b(exit(?:s|ed|ing)?\b.{0,40}\b(?:venture|business|market|plant|site)|divest(?:s|ed|ment|iture)?|"
     r"sell(?:s|ing)?\b.{0,30}\b(?:stake|business|plant|site)|"
     r"(?:permanent(?:ly)? )?clos(?:e|es|ed|ure|ing)\b.{0,30}\b(?:plant|unit|line|site|facility))\b"),
    ("CORPORATE", "corporate_venture",
     r"\b(joint venture|strategic (?:partner(?:ship)?|investor)|partner(?:s|ed|ship) with|"
     r"acquir(?:e|es|ed|ing|sition)|merger|takeover|stake in|capital increase)\b"),
    ("CAPACITY", "capacity_utilisation",
     r"\b(operating rate|capacity utilisation|capacity utilization|run rate)\b"),
    ("CAPACITY", "capacity_addition",
     r"\b(new (?:plant|unit|line|train|capacity|facility)|expansion|expand(?:s|ed|ing)? capacity|"
     r"debottleneck(?:ing)?|world[- ]scale|greenfield|brownfield|"
     r"(?:plans?|planned|to build|building|construct(?:s|ed|ing|ion)?)\b.{0,40}\b(?:plant|unit|line|train))\b"),
)

# Commentary: a market that already moved. A signal that arrives after the move is not a signal, and
# these pieces are the bulk of what an English commodity desk publishes.
SKIP = re.compile(
    r"\b(weekly review|monthly review|market (?:review|analysis|overview|summary)|"
    r"price (?:chart|index|trend)|trend analysis|analysis of|this week|last week|"
    r"import transaction analysis|sourcing requests)\b", re.I)

# Product families the application governs, as English desks write them. Ordered: the specific names
# win over the bare ticker, so "polymeric MDI" is never filed as pure MDI.
MATERIALS: tuple[tuple[str, str], ...] = (
    (r"\bTDI\b|\btoluene di-?isocyanate\b", "TDI"),
    (r"\bpolymeric MDI\b|\bPMDI\b|\bcrude MDI\b", "PMDI"),
    (r"\bpure MDI\b", "PURE_MDI"),
    (r"\bMDI\b|\bdiphenylmethane di-?isocyanate\b", "PMDI"),
    (r"\bpolyether\b|\bpolyol\b", "POLYOL"),
    (r"\bpropylene oxide\b|\bPO\b", "PO"),
    (r"\bethylene oxide\b|\bEO\b", "EO"),
    (r"\btoluene\b", "TOLUENE"),
    (r"\bbenzene\b", "BENZENE"),
    (r"\baniline\b", "ANILINE"),
    (r"\bnitric acid\b", "NITRIC_ACID"),
    (r"\bchlorine\b", "LIQUID_CHLORINE"),
    (r"\bDMF\b", "DMF"),
    (r"\badipic acid\b", "ADIPIC_ACID"),
    (r"\bBDO\b", "BDO"),
    (r"\bneopentyl glycol\b|\bNPG\b", "NPG"),
    (r"\bTPU\b", "TPU"),
    (r"\bpolyurethane\b", "POLYURETHANE"),
)


def material_of(title: str) -> str | None:
    """The governed product family a headline is about, or None when it names none of them."""
    for pattern, family in MATERIALS:
        if re.search(pattern, title, re.I):
            return family
    return None


def classify_event(title: str) -> tuple[str, str] | None:
    """(kind, record_type), or None when the headline is commentary rather than an event."""
    if SKIP.search(title):
        return None
    for kind, record_type, pattern in EVENT_RULES:
        if re.search(pattern, title, re.I):
            return kind, record_type
    return None
