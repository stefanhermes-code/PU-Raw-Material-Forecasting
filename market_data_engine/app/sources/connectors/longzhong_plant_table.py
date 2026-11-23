"""The plant table inside a Longzhong weekly maintenance bulletin.

WHAT THIS IS FOR. `mysteel_pu.parse_pu_events` reads the free headline stream and files a
PLANT_EVENT for a title like "[检修]：中国环氧丙烷装置检修周度数据统计（20261113）". That headline
says a bulletin exists. It does not say which plant, at what capacity, down since when - and that
is the whole of the supply-side signal. Stefan, 17 November 2026, comparing the store against a
third-party weekly review: "the plant bulletins are stored as headlines only."

WHAT IS ACTUALLY AVAILABLE. The bulletin body is served publicly at the article URL - HTTP 200,
no login, no member check (measured 17 November 2026 on the 13 and 6 November PO bulletins).
`mysteel_pu` carries a note from September saying article bodies are member-gated; that is no
longer true, and the note is corrected there rather than left to mislead the next reader.

What IS true is that the body is CUT. The publisher serves the first few rows of the table and
ends them with a single "…", after which a scan-login script takes over. Four or five plants of a
longer table, every time. So this parser reports `truncated` on every row set it builds from a cut
body, and nothing downstream may read a plant's absence as that plant running normally. The table
is a sample of the market, not a census of it.

THE SHAPE. The body is one unbroken run of characters - the publisher's table markup is flattened
before it is served, so there are no cells to read:

    表 2026年11月13日国内环氧丙烷装置检修表地区企业名称产能（万吨/年）装置情况
    东北航锦科技12装置9月10日4万吨小装置故障，半负荷检修，或持续至年底…
    华北天津渤化20装置9成负荷运行天津石化15装置8成负荷运行
    山东山东滨化2820万吨装置正常满负荷运行，11月3日起东瑞8万吨装置停车检修，预计14天
    山东鑫岳…

Region appears once per group and is carried down. Company and capacity have no separator at all,
and neither do two consecutive rows. The only reliable boundary is the capacity number, which is
why `_CAPACITY` below is the whole of the parse.

THE AMBIGUITY THAT MATTERS. "山东滨化2820万吨装置" is capacity 28 followed by a status that opens
"20万吨装置". Read greedily it is capacity 2820; read one digit back it is 282 followed by "0万吨",
which also scans. Both are wrong and both look right. The parse is pinned by requiring the status
to begin the way a status always begins - 装置, or a plant size written "<n>万吨" with n not zero -
and by refusing a capacity outside the range a real plant occupies. `test_binhua_is_twenty_eight_not_two_thousand`
is this case and nothing else.

UNITS. 产能（万吨/年）is ten-thousand tonnes per year, so 28 means 280 kt/yr. Both numbers are kept:
`capacity_kt_per_year` is what the rest of the application reads, `capacity_wan_t_per_year` is what
the publisher printed, so a conversion can be rechecked without the page.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

# The bulletins this parser is for. The weekly statistics posting is the one with a table; a
# one-plant news item ("[BDO]：新疆曙光绿华BDO装置动态") carries prose and is left to the headline
# stream, which already handles it.
_TABLE_HEADLINE = re.compile(r"装置检修周度数据统计")

# Material, from the bulletin's own title. Deliberately the same labels `mysteel_pu._MATERIALS`
# uses, and deliberately a short list: a bulletin for a chemical the store does not price has
# nothing to tell this application, and silently filing it under the wrong family would be worse
# than not reading it.
_MATERIALS: tuple[tuple[str, str], ...] = (
    ("环氧丙烷", "PO"), ("环氧乙烷", "EO"), ("聚醚", "POLYOL"), ("己二酸", "ADIPIC_ACID"),
    ("新戊二醇", "NPG"), ("苯胺", "ANILINE"), ("DMF", "DMF"), ("BDO", "BDO"),
    ("聚合MDI", "PMDI"), ("纯MDI", "PURE_MDI"), ("TDI", "TDI"), ("MDI", "PMDI"),
)

_ARTICLE_BODY = re.compile(
    r'<div[^>]*\bid=["\']article-content["\'][^>]*>(?P<body>.*?)</div>', re.S | re.I)
_TABLE_DATE = re.compile(r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日")
_HEADER = re.compile(r"地区\s*企业名称\s*产能（万吨/年）\s*装置情况")

# Regions as the publisher writes them: the seven broad regions, plus the provinces it uses as a
# region in its own right because they hold many plants.
#
# 天津, 上海, 北京, 重庆 and 浙江 are deliberately NOT here. They are municipalities and provinces
# that far more often BEGIN a company name in this industry - 天津石化, 上海石化, 浙江石化 - and the
# region is a label while the company is the row's identity. Read as a region, "天津石化" becomes a
# plant called 石化, which is wrong in the field that matters; left alone, a genuine 浙江 cell is
# carried into the company name and the previous region stands, which is wrong only in the label.
# When the two cannot both be right, the identity wins. `test_tianjin_petrochemical_is_a_company`
# holds this.
_REGIONS: tuple[str, ...] = (
    "东北", "华北", "华东", "华南", "华中", "西北", "西南",
    "山东", "江苏", "福建", "广东", "河北", "河南", "湖北", "湖南", "安徽",
    "新疆", "内蒙古", "宁夏", "陕西", "四川", "辽宁", "吉林", "黑龙江",
)

# A capacity is a number that a status follows. A status opens with 装置 / 大装置, with a plant size
# written "<n>万吨" where n is not zero, or with one of the handful of words the desk starts a
# sentence with. The leading [1-9] on the 万吨 branch is what stops "2820万吨" parsing as 282 + 0万吨.
_STATUS_OPENS = r"(?:大?装置|[1-9]\d*万吨|目前|本周|正常|计划|预计|暂|已|近期|月底|年底)"
_CAPACITY = re.compile(rf"(?P<cap>\d{{1,3}}(?:\.\d)?)(?={_STATUS_OPENS})")

# What a real unit occupies, in 万吨/年. China's largest single PO train is about 60; the biggest
# polyether complex in these tables is under 100. A number outside this is a date, a percentage or
# a misread boundary, never a capacity.
_CAPACITY_MIN, _CAPACITY_MAX = 1.0, 300.0

_LOAD_FULL = re.compile(r"满负荷|满负运行|满产")
_LOAD_FRACTION = re.compile(r"(?P<n>[一二三四五六七八九十\d]+)\s*成\s*负?荷?")
_LOAD_HALF = re.compile(r"半负")
_OUTAGE = re.compile(r"停车|检修|故障|停工|不可抗力")
_RESTART = re.compile(r"复产|复工|恢复生产|重启|开车")
_OUTAGE_FROM = re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日起")
_EXPECTED_DAYS = re.compile(r"预计\s*(?P<n>\d{1,3})\s*天")
_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                   "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def is_plant_table_bulletin(headline: str) -> bool:
    """True for the weekly statistics posting, which is the only one carrying a table."""
    return bool(_TABLE_HEADLINE.search(headline or ""))


# The OTHER weekly statistics posting: 产能利用率周数据统计. Same desk, same shape of page, and the
# one that is on the landing page most days - the maintenance bulletin is published on a Friday and
# has rotated off by Monday, which is how 17 November found none.
#
# EVERY DESK WORDS IT DIFFERENTLY. This was first written from the four bulletins in front of us -
# 产能利用率周数据统计 and 产能利用率周数据分析 - and that is a pattern for two desks, not for a
# posting. The propylene-oxide and TDI desks put the period after the statistic
# (产能利用率周数据统计); the polymeric-MDI desk puts it before (周度产能利用率数据统计); PTMEG
# names no period at all (产能利用率数据分析); trimethylolpropane ends on 情况; pure MDI is monthly
# (产能利用率月数据统计); and the neopentyl-glycol desk calls the statistic 开工率 outright
# (周度企业开工率统计). MEASURED, 18 November 2026: the narrow pattern read 25 of the 59
# capacity-utilisation rows in the live store and left 34 permanently blank.
#
# So the gate asks what the headline IS: the statistic itself, the desk's own few words, then the
# word for a statistical posting. Checked against every anchor on the 142 archived pages this
# source has collected - 4,479 distinct headlines - it matches 79 where the narrow one matched 43,
# every one of the 36 it adds is a genuine utilisation bulletin, and it loses none.
_UTILISATION_HEADLINE = re.compile(r"(?:产能利用率|开工率)[^，。：、]{0,6}?(?:数据)?(?:统计|分析|情况)")

# Read from the desk's own sentence, not from the table beside it. The table prints
# "产量12.7912.760.030.24%" - this period, last period, change and change rate with no separator -
# and 12.79/12.76 against 12.791/2.76 cannot be told apart by looking. The sentence says each
# number once with a word on either side of it, so it is the only honest place to read them.
#
# Each desk words that sentence its own way, and they are different people: the PO desk writes
# "产能利用率68.26%", the TDI desk "周开工率在79%", the BDO desk "开工率为52.2%". One pattern per
# phrasing, all measured against the real bulletins on 17 November 2026.
_RATE = re.compile(r"(?:产能利用率|开工率)\s*(?:在|为|是|：|:)?\s*(?P<rate>\d{1,3}(?:\.\d+)?)\s*%")
_OUTPUT = re.compile(
    r"(?:本期(?:总)?产量|周产量|行业产量|样本周?产量|产量)\s*(?:在|为|是|：|:)?\s*"
    r"(?P<output>\d{1,4}(?:\.\d+)?)\s*万吨")
# Two ways the same move is written. "较上期增加0.12个百分点" is explicit; "环比-1.8%" is the BDO
# desk's shorthand and is ALSO percentage points, despite the % sign - which is why it is only read
# when it sits immediately after the rate. The same bulletin says "较上周-3.33%" of its OUTPUT a
# clause later, and that one really is a percentage.
_RATE_CHANGE = re.compile(
    r"较上(?:期|周)(?P<dir>增加|提高|上升|下降|降低|减少)\s*(?P<pp>\d{1,3}(?:\.\d+)?)\s*个百分点")
_RATE_CHANGE_SHORT = re.compile(r"^[，,、\s]*环比\s*(?P<sign>[-+－＋]?)\s*(?P<pp>\d{1,3}(?:\.\d+)?)\s*%")
_PERIOD = re.compile(r"（(?P<from>20\d{6})\s*-\s*(?P<to>\d{4,8})）")


def is_utilisation_bulletin(headline: str) -> bool:
    """True for the weekly capacity-utilisation posting."""
    return bool(_UTILISATION_HEADLINE.search(headline or ""))


def parse_utilisation_bulletin(html: str, *, headline: str = "") -> dict | None:
    """The industry operating rate this bulletin reports, or None if it does not say.

    One number for a whole chemical in China, which is the supply side at the level a forecast can
    actually use: 68.26% of national propylene-oxide capacity running in the week to 12 November.

    Unlike the maintenance table, the figure is in the opening sentence and ahead of the cut, so
    this one is not truncated.
    """
    text = _body_text(html)
    if not text:
        return None
    rate = _RATE.search(text)
    if rate is None:
        return None
    out: dict = {
        "material": _material_of(headline or text[:120]),
        "operating_rate": round(float(rate.group("rate")) / 100.0, 4),
    }
    output = _OUTPUT.search(text)
    if output:
        out["output_wan_t"] = float(output.group("output"))
        out["output_kt"] = round(float(output.group("output")) * 10.0, 1)
    change = _RATE_CHANGE.search(text)
    if change:
        points = float(change.group("pp"))
        out["change_points"] = points if change.group("dir") in ("增加", "提高", "上升") else -points
    else:
        # Only what directly follows the rate, so the output's own "较上周-3.33%" cannot be read as
        # a move in the operating rate.
        short = _RATE_CHANGE_SHORT.match(text[rate.end():])
        if short:
            points = float(short.group("pp"))
            out["change_points"] = -points if short.group("sign") in ("-", "－") else points
    # The PO desk puts the week in the headline, the TDI desk puts it in the first sentence
    # ("本周（20261106-1112）") and leaves the headline with a single date. Both are the week the
    # rate is for, so both are read.
    period = _PERIOD.search(headline or "") or _PERIOD.search(text)
    if period:
        out["period"] = f"{period.group('from')}-{period.group('to')}"
    return out


def _material_of(title: str) -> str | None:
    for label, family in _MATERIALS:
        if label in title:
            return family
    return None


def _body_text(html: str) -> str:
    """The article body as flat text, or "" when the page carries no article container."""
    match = _ARTICLE_BODY.search(html)
    if match is None:
        return ""
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", match.group("body"))
    text = html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


def _load_fraction(status: str) -> float | None:
    """How hard the unit is running, 0.0-1.0, or None where the row does not say.

    "9成负荷" is 90%, "半负" is 50%, "满负荷" is 100%. A row that says a unit is down says nothing
    about load - the outage fields carry that - so this returns None rather than 0.0, which would
    claim a measurement the publisher did not make.
    """
    fraction = _LOAD_FRACTION.search(status)
    if fraction:
        raw = fraction.group("n")
        value = _CHINESE_DIGITS.get(raw, None)
        if value is None:
            try:
                value = int(raw)
            except ValueError:
                return None
        if 1 <= value <= 10:
            return round(value / 10.0, 2)
    if _LOAD_HALF.search(status):
        return 0.5
    if _LOAD_FULL.search(status):
        return 1.0
    return None


def _outage(status: str, year: int) -> dict | None:
    """The outage this row reports, or None.

    `from` is the date the publisher printed, carried to a full date with the bulletin's own year.
    `expected_days` is only ever the publisher's own "预计 n 天" - this never estimates a duration.
    """
    if not _OUTAGE.search(status):
        return None
    out: dict = {"state": "RESTARTING" if _RESTART.search(status) else "DOWN"}
    start = _OUTAGE_FROM.search(status)
    if start:
        try:
            out["from"] = datetime(year, int(start.group("m")), int(start.group("d")),
                                   tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    days = _EXPECTED_DAYS.search(status)
    if days:
        out["expected_days"] = int(days.group("n"))
    return out


def parse_plant_table(html: str, *, headline: str = "") -> dict:
    """Every plant row the served body carries.

    Returns {"table_date", "material", "truncated", "rows": [...]}. `rows` is empty - not an
    error - when the page is not a table bulletin or the body is missing; a caller that gets no
    rows keeps the headline event it already had and loses nothing.
    """
    text = _body_text(html)
    if not text:
        return {"table_date": None, "material": None, "truncated": False, "rows": []}

    title = headline or text[:120]
    day = _TABLE_DATE.search(text)
    table_date = None
    year = datetime.now(timezone.utc).year
    if day:
        try:
            table_date = datetime(int(day.group("y")), int(day.group("m")), int(day.group("d")),
                                  tzinfo=timezone.utc)
            year = table_date.year
        except ValueError:
            table_date = None

    header = _HEADER.search(text)
    if header is None:
        return {"table_date": table_date, "material": _material_of(title),
                "truncated": False, "rows": []}
    body = text[header.end():]

    # "…" is the publisher's cut. Everything after it is a fragment of a company name and must not
    # become a row; everything before it is complete.
    truncated = "…" in body or "..." in body
    if truncated:
        body = re.split(r"…|\.\.\.", body, maxsplit=1)[0]

    # One pass for the boundaries, keeping each accepted row paired with the match that found it,
    # so a rejected capacity cannot shift the status text of the rows around it.
    accepted: list[tuple[re.Match, float]] = [
        (match, float(match.group("cap")))
        for match in _CAPACITY.finditer(body)
        if _CAPACITY_MIN <= float(match.group("cap")) <= _CAPACITY_MAX
    ]

    # Names and statuses fall out of one walk. A row's status runs from its capacity number to the
    # next one, and what `_without_trailing_name` cuts off the end of it IS the next row's name -
    # so the two boundaries are derived from each other rather than guessed twice and disagreeing.
    names: list[str] = [body[:accepted[0][0].start()]] if accepted else []
    statuses: list[str] = []
    for index, (match, _capacity) in enumerate(accepted):
        to = accepted[index + 1][0].start() if index + 1 < len(accepted) else len(body)
        raw = body[match.end():to]
        status = _without_trailing_name(raw)
        statuses.append(status)
        if index + 1 < len(accepted):
            names.append(raw[len(status):])

    rows: list[dict] = []
    region = None
    for (match, capacity), name, status in zip(accepted, names, statuses):
        name = name.strip(" ，,、")
        # The region cell is printed once per group and carried down, so a region token at the
        # front of a name is only a cell when it is a DIFFERENT region from the one in force.
        # "山东滨化" opens the 山东 group and reads "山东山东滨化"; "山东鑫岳", the next row in that
        # same group, prints no cell - and stripping 山东 off it would file the company as 鑫岳.
        for candidate in _REGIONS:
            if name.startswith(candidate) and candidate != region and name[len(candidate):].strip(" ，,、"):
                region, name = candidate, name[len(candidate):]
                break
        name = name.strip(" ，,、")
        if not name:
            continue
        rows.append({
            "region": region,
            "company": name,
            "capacity_wan_t_per_year": capacity,
            "capacity_kt_per_year": round(capacity * 10.0, 1),
            "status": status.strip(" ，,、"),
        })

    for row in rows:
        row["load_fraction"] = _load_fraction(row["status"])
        row["outage"] = _outage(row["status"], year)

    return {"table_date": table_date.isoformat() if table_date else None,
            "material": _material_of(title), "truncated": truncated, "rows": rows}


# A status ends where the next company's name starts, and nothing in the text marks that boundary.
# What CAN be recognised is the word a status ends on - 运行, 停车, 检修, 满产, "14天", 年底 - with
# the next company's name left over after it.
#
# `.*` is greedy on purpose, so the LAST such word wins. Non-greedy took the first, which in
# "装置9成负荷运行天津石化" is 负荷 - leaving "运行天津石化" to be read as a company name. The trailing
# name is REQUIRED here for the same reason: made optional, a greedy status simply swallows the
# whole string and the boundary is never found.
_STATUS_TAIL = re.compile(
    r"^(?P<status>.*(?:运行|运转|停车|检修|故障|停工|复产|开车|满产|负荷|\d+天|月底|年底|不可抗力))"
    r"(?P<name>[一-鿿]{2,12})$", re.S)


def _without_trailing_name(status: str) -> str:
    """The status without the next row's company name on its tail.

    Two rows run together with no separator at all, so a status read up to the next capacity number
    carries the following company (and sometimes its region) on the end. A status with no name
    after it - the last row of a table - is returned unchanged.
    """
    match = _STATUS_TAIL.match(status.strip(" ，,、"))
    if match is None:
        return status
    return match.group("status")
