"""Listed TDI and MDI producers read through their exchange filings.

A trade publisher reports a plant event when it hears about it. A listed company files one because it
is obliged to, on the day, under its own name, with the capacity and the money stated. For capacity
coming and going - which is the long-horizon half of market intelligence - this is the primary record
and everything else is a retelling of it.

Three issuers are read, all on the Shanghai exchange:

  600230  沧州大化   Cangzhou Dahua - TDI
  600309  万华化学   Wanhua Chemical - TDI and MDI
  600426  山东华鲁恒升 Hualu Hengsheng - upstream and PU chain

The filing index at sse.com.cn is a JavaScript shell; the announcements come from the site's own JSON
endpoint, which the page itself calls, unauthenticated, with no token. Reading the same endpoint the
page reads is ordinary fetching - there is no protection here being studied or worked around. The
endpoint does require the Referer the site sends, which is set below.

Titles, dates and the PDF link are kept. Filing documents are not downloaded: the title states the
event, and a filing PDF is a document this engine has no right to redistribute.

NOT YET RUN AGAINST THE LIVE ENDPOINT. The cloud workspace's egress does not reach query.sse.com.cn,
so this has been exercised only against captured response shapes. It must be run on the collecting
machine and its output read before anything from it is shown to a customer.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from app.domain.schemas import ExtractedRecord, SourceItem

_ENDPOINT = "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do"
_REFERER = "https://www.sse.com.cn/"
_PDF_BASE = "https://static.sse.com.cn"

ISSUERS: dict[str, tuple[str, str]] = {
    # code: (English name, the chemical it is read for)
    "600230": ("Cangzhou Dahua", "TDI"),
    "600309": ("Wanhua Chemical", "TDI"),
    "600426": ("Hualu Hengsheng", "POLYURETHANE"),
}

# What a filing title has to say before it counts as an event. A results announcement or a board
# resolution about an auditor is a filing, not market intelligence.
_RULES: tuple[tuple[str, str, str], ...] = (
    ("PLANT_EVENT", "outage", r"停产|停车|检修|事故|故障|不可抗力"),
    ("PLANT_EVENT", "restart", r"复产|复工|恢复生产|投料开车"),
    ("CORPORATE", "capacity_investment", r"投资|募集资金.{0,12}项目|增资扩产"),
    ("CORPORATE", "corporate_venture", r"合资|收购|并购|股权收购|增资|参股|战略合作"),
    ("CORPORATE", "corporate_exit", r"转让股权|出售股权|剥离|退出|关停|注销"),
    ("CAPACITY", "capacity_addition", r"新建|扩建|扩产|投产|新增产能|装置.{0,8}(?:建成|投运|试车)"),
)
_MATERIALS: tuple[tuple[str, str], ...] = (
    ("TDI", "TDI"), ("聚合MDI", "PMDI"), ("纯MDI", "PURE_MDI"), ("MDI", "PMDI"),
    ("聚醚", "POLYOL"), ("多元醇", "POLYOL"), ("环氧丙烷", "PO"), ("甲苯", "TOLUENE"),
    ("苯胺", "ANILINE"), ("硝酸", "NITRIC_ACID"), ("液氯", "LIQUID_CHLORINE"),
    ("聚氨酯", "POLYURETHANE"),
)
_JSONP_RE = re.compile(r"^[^(]*\((?P<body>.*)\)\s*;?\s*$", re.S)


def classify_filing(title: str) -> tuple[str, str] | None:
    for kind, record_type, pattern in _RULES:
        if re.search(pattern, title):
            return kind, record_type
    return None


def material_of(title: str) -> str | None:
    for label, family in _MATERIALS:
        if label in title:
            return family
    return None


def parse_payload(body: str) -> list[dict]:
    """Announcement rows from the endpoint's response, whether it answers JSON or JSONP.

    The endpoint has answered in both shapes over time and the field names have varied in case, so
    keys are read case-insensitively. A response we cannot read returns no rows rather than raising:
    a filing lane going quiet is visible in the run report, and a crash takes the whole run with it.
    """
    text = (body or "").strip()
    match = _JSONP_RE.match(text)
    if match:
        text = match.group("body")
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    rows = payload.get("result") or payload.get("pageHelp", {}).get("data") or []
    out: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        lowered = {str(key).lower(): value for key, value in row.items()}
        title = str(lowered.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "date": str(lowered.get("ssedate") or lowered.get("bulletin_date") or "").strip(),
            "url": str(lowered.get("url") or "").strip(),
        })
    return out


def filing_date(value: str) -> datetime | None:
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value or "")
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_events(body: str, *, code: str) -> list[dict]:
    """Filing rows that describe an event, in the engine's event vocabulary."""
    producer, default_material = ISSUERS.get(code, ("", None))
    out: list[dict] = []
    seen: set[str] = set()
    for row in parse_payload(body):
        title = row["title"]
        if title in seen:
            continue
        classified = classify_filing(title)
        if classified is None:
            continue
        seen.add(title)
        kind, record_type = classified
        url = row["url"]
        out.append({
            "headline": title,
            "kind": kind,
            "record_type": record_type,
            # The issuer's own chemical when the title names none: a Cangzhou Dahua filing about a
            # shutdown is about TDI, because that is what Cangzhou Dahua makes.
            "material": material_of(title) or default_material,
            "event_date": filing_date(row["date"]),
            "producer": producer,
            "url": url if url.startswith("http") else _PDF_BASE + "/" + url.lstrip("/"),
        })
    return out


# How far back a run reaches. The filing lane is about what changed recently; a longer window just
# re-reads announcements the store already has.
WINDOW_DAYS = 180

# The endpoint is undocumented, and two runs have now answered cleanly with "total": 0 - once with no
# date range, once with one. A clean zero to a query the server accepted means the QUERY is wrong,
# not the connection, and guessing a third parameter set in the dark would be the third guess.
#
# So the connector asks in several ways in one run and records which one answered. Each variant is an
# ordinary GET of the same public endpoint; there is nothing here being probed or worked around, and
# the first variant that returns rows is the one used. When none return rows, the diagnostics carry
# every variant with what it answered, which is what a decision needs.
VARIANTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("periodic+dates", {"reportType2": "DQBG", "reportType": "ALL", "dates": "yes",
                        "securityType": "0101,120100,020100,020200,120200"}),
    ("all+dates", {"reportType": "ALL", "dates": "yes",
                   "securityType": "0101,120100,020100,020200,120200"}),
    ("all+dates+a-share-only", {"reportType": "ALL", "dates": "yes", "securityType": "0101"}),
    ("all+no-dates+a-share-only", {"reportType": "ALL", "dates": "no", "securityType": "0101"}),
    ("securityCode-instead-of-productId", {"reportType": "ALL", "dates": "yes",
                                           "securityType": "0101", "key": "securityCode"}),
)


def query_url(code: str, *, page_size: int = 25, today: datetime | None = None,
              window_days: int = WINDOW_DAYS, variant: dict[str, str] | None = None) -> str:
    """One call to the endpoint, built from a variant's parameters."""
    options = dict(variant or VARIANTS[0][1])
    end = (today or datetime.now(timezone.utc)).date()
    begin = end - timedelta(days=window_days)
    key = options.get("key", "productId")
    parts = [
        "isPagination=true",
        f"{key}={code}",
        "keyWord=",
        "securityType=" + quote(options.get("securityType", "0101"), safe=""),
        "reportType=" + options.get("reportType", "ALL"),
    ]
    if options.get("reportType2"):
        parts.append("reportType2=" + options["reportType2"])
    if options.get("dates") == "yes":
        parts.append(f"beginDate={begin.isoformat()}&endDate={end.isoformat()}")
    else:
        parts.append("beginDate=&endDate=")
    parts.append(f"pageHelp.pageSize={page_size}&pageHelp.pageNo=1&pageHelp.beginPage=1"
                 "&pageHelp.cacheSize=1&pageHelp.endPage=1")
    return f"{_ENDPOINT}?" + "&".join(parts)


class ExchangeFilingsConnector:
    """Shanghai exchange filings for one issuer, read as an event lane."""

    allow_empty_parse = True

    def __init__(self, code: str):
        if code not in ISSUERS:
            raise KeyError(f"no issuer configured for {code}")
        self.code = code
        self.producer, self.default_material = ISSUERS[code]
        self.source_id = f"sse_{code}_filings"
        self.source_url = query_url(code)

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        # The site sends this on its own calls and the endpoint answers "系统繁忙" without it.
        "Referer": _REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    def fetch_item(self, item: SourceItem) -> bytes:
        """Ask each way in turn, keep the first answer that carries filings, record them all."""
        self._attempts = []
        answered = b""
        for name, options in VARIANTS:
            url = query_url(self.code, variant=options)
            try:
                response = httpx.get(url, follow_redirects=True, timeout=30.0, headers=self._HEADERS)
                response.raise_for_status()
                body = response.content
            except Exception as exc:
                self._attempts.append({"variant": name, "url": url, "error": str(exc)[:200]})
                continue
            text = body.decode("utf-8", errors="ignore")
            rows = parse_payload(text)
            self._attempts.append({"variant": name, "url": url, "bytes": len(text),
                                   "rows": len(rows), "head": text[:400]})
            if rows:
                return body
            answered = answered or body
        # Nothing carried filings. The last clean answer is returned so the run reports SUCCESS with
        # no records rather than an error: the endpoint is reachable, the question is still wrong.
        return answered

    def extract_html(self, html: str, source_url: str | None = None) -> list[ExtractedRecord]:
        src = source_url or self.source_url
        events = parse_events(html, code=self.code)
        self._record_response(html, events)
        return [self._event_record(src, event) for event in events]

    def _record_response(self, body: str, events: list[dict]) -> None:
        """What the endpoint actually answered, written down so a silent zero can be explained.

        The first live run returned no filings for all three issuers and there was no way to tell
        whether that meant an error page, a changed field name, or genuinely no filings. Failure here
        is swallowed: a diagnostic must never be able to break a collection.
        """
        try:
            from app.producer_signals.export import default_feed_path

            rows = parse_payload(body)
            folder = default_feed_path().parent
            folder.mkdir(parents=True, exist_ok=True)
            payload = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source_id": self.source_id,
                "url": self.source_url,
                "bytes": len(body),
                "rows_in_response": len(rows),
                "events_kept": len(events),
                "titles": [row["title"] for row in rows[:40]],
                "variants_tried": getattr(self, "_attempts", []),
                # The first slice of the raw answer, which is where an error page announces itself.
                "response_head": body[:2000],
            }
            (folder / f"filing_diagnostics_{self.source_id}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            return

    def extract(self, item: SourceItem, content: bytes) -> list[ExtractedRecord]:
        return self.extract_html(content.decode("utf-8", errors="ignore"), item.source_url)

    def _event_record(self, src: str, event: dict) -> ExtractedRecord:
        return ExtractedRecord(
            kind="event",
            source_url=src,
            publication_date=event["event_date"],
            data={
                "type": event["record_type"],
                "event_type": event["kind"],
                "headline": event["headline"],
                "material": event["material"],
                "product": event["material"],
                "market_geography": "China",
                "publisher": f"SSE {self.code}",
                "actor": event["producer"],
                "url": event["url"],
                "observation_date": event["event_date"],
            },
        )
