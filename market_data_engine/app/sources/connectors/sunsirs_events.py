"""Market-intelligence events from SunSirs' free English commodity news.

Why this exists. Signal verification is only worth anything if a signal can be corroborated, and
corroboration needs *two independent publishers*. Until now the engine had exactly one event
publisher - Longzhong, which Mysteel also fronts - so nothing could ever corroborate anything and
every anonymous headline died unattributed. SunSirs (生意社, sunsirs.com) is a genuinely separate
publisher: different company, different desk, its own assessments, and the engine already fetches it
successfully for prices, so this costs no new acquisition risk.

Independence caveat, kept where it will be read: SunSirs and 100ppi are ONE ecosystem
(`SUNSIRS_100PPI` in `producer_signals.verification`). Events parsed here corroborate Longzhong,
ECHEMI, SCI99 and the producers - never 100ppi.

Only the headline, its date, its link and the publisher are kept. The article body is SunSirs'
copyrighted text and is not stored.
"""
from __future__ import annotations

import html as html_lib
import re

from app.sources.connectors.event_rules_en import classify_event, material_of  # noqa: F401

# The event vocabulary, the commentary filter and the product names live in `event_rules_en` and are
# shared with every English publisher the engine reads. They used to live here, which meant a rule
# added for SunSirs was invisible to ECHEMI and the same event reported by both could never be
# matched - corroboration silently failing is worse than not having it.

# SunSirs article links, both the current and the legacy path shape.
_ARTICLE_HREF_RE = re.compile(r"(?:detail_news-|commodity-news/petail-)", re.I)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>[^<]{8,180})</a>', re.I)

# "SunSirs:" prefixes almost every headline and says nothing about the event.
_PREFIX_RE = re.compile(r"^\s*SunSirs\s*[:：]\s*", re.I)


def clean_headline(title: str) -> str:
    return _PREFIX_RE.sub("", html_lib.unescape(title)).strip()



def is_article_url(url: str) -> bool:
    return bool(_ARTICLE_HREF_RE.search(url or ""))


def article_title(html: str) -> str:
    """The article's own headline, from <h1> where SunSirs puts it, else the document title."""
    for pattern in (r"<h1\b[^>]*>(?P<t>.*?)</h1>", r"<title\b[^>]*>(?P<t>.*?)</title>"):
        match = re.search(pattern, html, re.I | re.S)
        if match:
            text = re.sub(r"<[^>]+>", " ", match.group("t"))
            text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
            text = re.split(r"\s*[-|]\s*SunSirs\b", text, maxsplit=1)[0].strip()
            if len(text) >= 8:
                return clean_headline(text)
    return ""


def parse_index_events(html: str) -> list[dict]:
    """Event headlines linked from a SunSirs news index. Headline / kind / record_type / material / url."""
    out: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(html):
        href = html_lib.unescape(match.group("href"))
        if not is_article_url(href):
            continue
        title = clean_headline(match.group("title"))
        if not title or title in seen:
            continue
        classified = classify_event(title)
        if classified is None:
            continue
        seen.add(title)
        kind, record_type = classified
        out.append({
            "headline": title,
            "kind": kind,
            "record_type": record_type,
            "material": material_of(title),
            "url": href if href.startswith("http") else "https://www.sunsirs.com" + "/" + href.lstrip("/"),
        })
    return out
