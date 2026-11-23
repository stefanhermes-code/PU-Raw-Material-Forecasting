"""Reading the prices out of a document, so nobody has to map columns by hand.

Stefan, 7 October 2026, looking at the first version of the import screen with his own monitor
document loaded: six dropdowns to fill, every suggestion empty, fourteen rows read and none of them
complete. He was right. A form that asks a reader to describe their own document is not an import.

So the document goes to a model, which returns rows. Stefan settled on 5 October that the document
may leave the machine, and the key is the one PU ExpertCenter already uses.

The guard against an invented number is not trust, it is the quote. Every row must carry the exact
text it was read from, and a row whose quote is not found in the document is dropped before anybody
sees it. A model that makes a price up has to make up the sentence it came from as well, and that
is checkable. What survives is still a proposal: Stefan ticks each row, and the engine only stores
what he ticked.

Nothing here writes, and nothing here fills a blank. A field the document does not state comes back
empty and the row waits for a person, exactly as it does on the hand-mapped path.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from raw_material_forecasting.price_import import (OBSERVATION_TYPES, ProposedRow, REQUIRED,
                                                   parse_day, parse_number)
from raw_material_forecasting.market_basis import canonical_basis
from raw_material_forecasting import units
from raw_material_forecasting import price_kinds

DEFAULT_MODEL = "gpt-4.1"
KEY_NAME = "OPENAI_API_KEY"
# Enough of a long report to hold its price tables; a monitor document runs to a few thousand words.
MAX_CHARACTERS = 120_000

INSTRUCTIONS = """You are reading a document for a polyurethane raw-material price database.

Return every price observation the document states. One object per price, per region, per date.

Rules, in order of importance:
1. Never invent. Every field must come from the document. If the document does not state something,
   use null. Do not infer a currency, an Incoterm, a date or a region from context or from another
   row.
2. Quote. Every object must carry "quote": the exact run of characters from the document that the
   price was read from, copied character for character. A row without a verbatim quote is discarded.
3. A price range, such as a low and a high separated by a dash, is one observation: put the low in "price_low" and the high
   in "price_high". A single price goes in both.
4. Do not convert anything. Report the currency and the number as written.
5. Ignore tables that are not prices: source registers, coverage summaries, announcement logs
   describing a change without stating a price level.

Fields per object:
  chemical          - the material as written, e.g. "Polyether polyol", "TDI", "Polymeric MDI"
  grade             - grade or specification if stated, else null
  geography         - the region or country as written
  basis             - the Incoterm or delivery term if stated, else null
  observation_date  - as written, e.g. "Aug 2026", "30 Sep 2026"
  currency          - the currency code as written
  unit              - the unit as written, exactly as the document spells it
  price_low         - number only, no separators
  price_high        - number only, no separators
  observation_type  - one of: market_price, contract_price, transaction_price, producer_price,
                      formula_based, quarterly_average. Choose only if the document makes it plain,
                      else null.
  publisher         - who published the price, if the document says
  quote             - the verbatim text

Answer with JSON only: {"rows": [ ... ]}."""


class ReaderUnavailable(RuntimeError):
    """No key, no package, or the model could not be reached. Said plainly on the screen."""


def api_key() -> str:
    return os.environ.get(KEY_NAME, "").strip()


def configured() -> bool:
    return bool(api_key())


def _document_text(document) -> str:
    """The document as the model sees it: its tables written out, then its running text."""
    parts: list[str] = []
    for sheet in getattr(document, "tables", []) or []:
        parts.append(f"### {sheet.name}")
        parts.append(" | ".join(sheet.columns))
        for row in sheet.rows:
            parts.append(" | ".join(row))
        parts.append("")
    text = (getattr(document, "text", "") or "").strip()
    if text:
        parts.append("### text")
        parts.append(text)
    return "\n".join(parts)[:MAX_CHARACTERS]


def _normalise(raw: dict[str, Any], haystack: str) -> ProposedRow | None:
    """One answered object as a proposed row, or nothing if its quote is not in the document."""
    quote = str(raw.get("quote") or "").strip()
    if not quote or _flatten(quote) not in haystack:
        return None
    values: dict[str, Any] = {}
    for field_name, key in (("chemical", "chemical"), ("geography", "geography"),
                            ("basis", "basis"), ("packaging", "packaging"),
                            ("currency", "currency"), ("unit", "unit")):
        value = str(raw.get(key) or "").strip()
        if value and value.lower() not in ("null", "none", "not stated", "n/a", "-"):
            values[field_name] = value
    day = parse_day(str(raw.get("observation_date") or ""))
    if day:
        values["observation_date"] = day
    low = parse_number(str(raw.get("price_low") if raw.get("price_low") is not None else ""))
    high = parse_number(str(raw.get("price_high") if raw.get("price_high") is not None else ""))
    prices = [value for value in (low, high) if value]
    if prices:
        # A range is one market with two ends. The midpoint is what a series carries, and the ends
        # stay in the quote beside it so nothing is hidden.
        values["price"] = sum(prices) / len(prices)
    kind = str(raw.get("observation_type") or "").strip()
    # A published price is a market price unless the document says it is something else. The model
    # is told to name a kind only when the document makes it plain, which it almost never does, so
    # every row arrived without one and was held back: 23 of 23 on the 6 October 2026 Price
    # Monitor, and not one import ever reached the store. Stefan's rule for the whole application:
    # no gates that make no sense. The kind stays editable on screen.
    values["observation_type"] = price_kinds.kind_of(kind)
    # No Incoterm in the document is a fact about the quote, not a gap in it. Stefan, 19 October
    # 2026: keep the price under Unspecified rather than drop it. Editable on screen.
    # A delivery term is one of a known set or it is Unspecified, never the document's own prose.
    # The Price Monitor of 20 October 2026 proposed "delivery: Linyi; formal Incoterm n/s", and
    # `commercial_basis` is part of an identity's comparability group - that wording would have
    # opened a market holding one price, joined to nothing. The wording itself is not lost: it is
    # in the quote beside the row. Stefan, 22 October 2026: "Inventing new Terms (the only other
    # real INCO term allowed is Unspecified)."
    values["basis"] = canonical_basis(values.get("basis", ""))
    # Every spelling of a metric tonne becomes the one token the store keeps, which `units`
    # decides - this module used to keep a seventh-spelling list of its own, two spellings short
    # of the store's.
    values["unit"] = units.canonical_unit(values.get("unit", ""))
    problems: list[str] = []
    if low and high and high < low:
        problems.append("the range reads high to low")
    missing = tuple(name for name in REQUIRED if not values.get(name))
    row = ProposedRow(values=values, source_row=0, missing=missing, problems=tuple(problems))
    row.values["quote"] = quote
    row.values["publisher"] = str(raw.get("publisher") or "").strip()
    row.values["grade"] = str(raw.get("grade") or "").strip()
    return row


UNSPECIFIED_BASIS = "UNSPECIFIED"
UNSPECIFIED_BASIS_WORDS = {"not stated", "not specified", "unspecified", "unknown", "n/a", "na", "none", "-"}


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def rows_from_answer(answer: str, document) -> list[ProposedRow]:
    """The model's answer as proposed rows, with every unquoted row dropped.

    Split out from the call so the checking can be tested without a network and without a key.
    """
    try:
        payload = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise ReaderUnavailable(f"the reader's answer was not readable: {exc}") from exc
    haystack = _flatten(_document_text(document))
    # A price the document quotes without its own date carries the document's date: a report is a
    # statement on the day it is issued. Taken from the file name when that leads with one
    # ("2026-10-06 - Global PU Raw Material Price Monitor.docx"); still editable on screen.
    document_day = document_date(getattr(document, "name", "") or "")
    out: list[ProposedRow] = []
    for index, raw in enumerate(payload.get("rows") or [], start=1):
        if not isinstance(raw, dict):
            continue
        row = _normalise(raw, haystack)
        if row is None:
            continue
        if not row.values.get("observation_date") and document_day:
            row.values["observation_date"] = document_day
            row.missing = tuple(name for name in row.missing if name != "observation_date")
        row.source_row = index
        out.append(row)
    return out


def document_date(name: str) -> str | None:
    """The day a file name leads with, as ISO, or None."""
    match = re.match(r"\s*(20\d{2})[-_.](\d{2})[-_.](\d{2})", str(name or ""))
    if not match:
        return None
    try:
        from datetime import date
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def read_rows(document, *, model: str = DEFAULT_MODEL, client=None) -> list[ProposedRow]:
    """Ask the model for every price in this document. `client` is for tests."""
    body = _document_text(document)
    if not body.strip():
        return []
    if client is None:
        key = api_key()
        if not key:
            raise ReaderUnavailable(
                f"{KEY_NAME} is not set in the engine's environment, so the document cannot be "
                "read automatically. The columns can still be mapped by hand below.")
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise ReaderUnavailable(
                "the openai package is not installed in this environment") from exc
        client = OpenAI(api_key=key)
    try:
        answer = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[{"role": "system", "content": INSTRUCTIONS},
                      {"role": "user", "content": body}],
        )
        text = answer.choices[0].message.content
    except ReaderUnavailable:
        raise
    except Exception as exc:
        raise ReaderUnavailable(f"the reader could not be reached: {exc}") from exc
    return rows_from_answer(text or "{}", document)
