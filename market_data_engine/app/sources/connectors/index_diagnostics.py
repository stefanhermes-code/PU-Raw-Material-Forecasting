"""A bounded record of what a news index actually offered on a run.

A parser written against markup nobody has seen is a guess, and a guess that silently matches nothing
looks exactly like a quiet day in the market. Several of these publishers cannot be read from outside
the machine that collects them - the cloud workspace's egress does not reach them, and SunSirs answers
a cloud address with a security interstitial - so without this the only way to tune a parser is to
imagine the HTML.

So every index collection writes down what it saw: how many links there were, which of them looked
like articles, and what each headline was classified as. Headlines and links only; no article text,
and never more than a page's worth.

Failure here is swallowed on purpose. A diagnostic that can break a collection is worse than no
diagnostic at all.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone

_RAW_CAP = 600_000

_RAW_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def write(source_id: str, index_url: str, html: str, *, is_article, classify, kept: list[dict]) -> None:
    try:
        from app.producer_signals.export import default_feed_path

        anchors = _RAW_ANCHOR_RE.findall(html)[:400]
        candidates = []
        for href, anchor_html in anchors:
            title = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", anchor_html))).strip()
            if not title or not is_article(href):
                continue
            classified = classify(title)
            candidates.append({
                "href": href,
                "title": title[:180],
                "classified_as": classified[0] if classified else None,
            })
        payload = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_id": source_id,
            "index_url": index_url,
            "bytes": len(html),
            "anchors_total": len(anchors),
            "article_links": len(candidates),
            "events_kept": len(kept),
            "candidates": candidates[:80],
        }
        folder = default_feed_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"index_diagnostics_{source_id}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # The page exactly as the server sent it. A list of anchors says a parser matched nothing;
        # only the markup says why, and these pages cannot be fetched from anywhere but this machine.
        (folder / f"index_raw_{source_id}.html").write_text(html[:_RAW_CAP], encoding="utf-8")
    except Exception:
        return
