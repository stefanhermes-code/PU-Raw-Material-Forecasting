"""Reading the developments out of a report that was written as prose.

The weekly briefing is read by rules because every item in it is labelled: a numbered heading, a
Status line, a Scope line. The Global PU Raw Material Price Monitor says the same kind of thing in
paragraphs - "Stepan's primary announcement states that STEPANPOL and TERATE rigid polyester
polyols in North America increase by USD 0.09/lb effective 1 October 2026" - where the chemical,
the region, the date and the confidence are all present and none of them are labelled. A rule
cannot pick those out without guessing, and guessing is the one thing this application does not do.

So this document goes to a model, exactly as a document full of prices does, and is held to the
same guard: every row must carry the verbatim text it was read from, and a row whose quote is not
in the document is dropped before anybody sees it. A model that invents a producer increase has to
invent the sentence it came from too, and that is checkable.

Two things it is told never to return, because they would quietly corrupt what we already hold.
Prices: this report quotes assessments of series the engine collects itself, and reading them in
here would count one market twice through somebody else's reading of it. And anything the document
attributes to a named assessor or news desk, because a customer report names no source anywhere -
the register refuses those at the gate as well, but it is cheaper not to propose them.
"""
from __future__ import annotations

import json
import re
from typing import Any

from raw_material_forecasting.briefing_reader import (CONFIRMED, INDICATION, UNCONFIRMED,
                                                      chemicals_in, geographies_in, kind_of)
from raw_material_forecasting.document_reader import (DEFAULT_MODEL, KEY_NAME, MAX_CHARACTERS,
                                                      ReaderUnavailable, api_key, configured)
from raw_material_forecasting.price_import import parse_day

INSTRUCTIONS = """You are reading a polyurethane market report for a register of DEVELOPMENTS:
things that happened in the market. Not prices.

Return one object per development the document states. A development is a producer price
announcement, a plant shutdown or restart, an incident, a capacity decision, a regulatory action,
a corporate move, or a trade shift.

Rules, in order of importance:
1. Never invent. Every field comes from the document. What it does not state is null.
2. Quote. Every object carries "quote": the exact run of characters the development was read from,
   copied character for character. An object without a verbatim quote is discarded.
3. Return no prices and no price levels as developments. A sentence that only reports what
   something costs is not a development. A producer ANNOUNCING an increase is a development, and
   the amount belongs in the headline where the document states it.
4. Return nothing whose only support in the document is an attribution to a price assessor or a
   news desk. If the document says a market assessment showed something, leave it out. If it says
   a producer, a company or an authority announced or did something, keep it.

Fields per object:
  headline     - one complete sentence stating what happened, in the document's own words as far
                 as possible, naming the company where the document names it
  chemicals    - the materials affected, as the document writes them, as a list
  regions      - the regions affected, as the document writes them, as a list
  happened_on  - the date the event takes effect or occurred, as written, else null
  confidence   - "confirmed" when the document rests it on a primary or official announcement,
                 "indication" when it rests on a report, a market view or an assessment,
                 "unconfirmed" when the document itself says it is unconfirmed or not decided
  context      - why it matters, in one or two sentences, from the document
  implication  - what the document says a buyer should do about it, if it says anything
  quote        - the verbatim text

Answer with JSON only: {"rows": [ ... ]}."""

CONFIDENCE = {"confirmed": CONFIRMED, "indication": INDICATION, "unconfirmed": UNCONFIRMED}


class Proposed:
    """One development a model proposed, with the text it came from."""

    def __init__(self, headline: str, chemicals, geographies, day, confidence: str,
                 context: str, implication: str, quote: str, kind: str = ""):
        self.headline = headline
        self.chemicals = tuple(chemicals)
        self.geographies = tuple(geographies)
        self.day = day
        self.confidence = confidence
        self.context = context
        self.implication = implication
        self.quote = quote
        self.kind = kind

    @property
    def prints_for_a_customer(self) -> bool:
        return self.confidence in (CONFIRMED, INDICATION)

    def __repr__(self) -> str:                       # for a failing test to be readable
        return f"Proposed({self.headline[:40]!r}, {self.chemicals}, {self.confidence})"


def looks_like_a_monitor(text: str) -> bool:
    head = (text or "")[:3000].lower()
    return "raw material price monitor" in head or (
        "material changes" in head and "source coverage" in head)


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _document_text(document) -> str:
    parts: list[str] = []
    for sheet in getattr(document, "tables", []) or []:
        parts.append(" | ".join(sheet.columns))
        for row in sheet.rows:
            parts.append(" | ".join(row))
    text = (getattr(document, "text", "") or "").strip()
    if text:
        parts.append(text)
    return "\n".join(parts)[:MAX_CHARACTERS]


def rows_from_answer(answer: str, document) -> list[Proposed]:
    """The model's answer as proposed developments, with every unquoted row dropped."""
    try:
        payload = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise ReaderUnavailable(f"the reader's answer was not readable: {exc}") from exc
    haystack = _flatten(_document_text(document))
    out: list[Proposed] = []
    for raw in payload.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("quote") or "").strip()
        headline = _flatten(raw.get("headline"))
        if not quote or not headline or _flatten(quote) not in haystack:
            continue
        said = " ".join(str(part) for part in (raw.get("chemicals") or []))
        where = " ".join(str(part) for part in (raw.get("regions") or []))
        day = parse_day(str(raw.get("happened_on") or ""))
        out.append(Proposed(
            headline=headline,
            chemicals=chemicals_in(said),
            geographies=geographies_in(where),
            day=day,
            confidence=CONFIDENCE.get(str(raw.get("confidence") or "").strip().lower(), ""),
            context=_flatten(raw.get("context")),
            implication=_flatten(raw.get("implication")),
            quote=quote,
            kind=kind_of(headline) or kind_of("", _flatten(raw.get("context"))),
        ))
    return out


def read_developments(document, *, model: str = DEFAULT_MODEL, client=None) -> list[Proposed]:
    """Ask the model for every development in this document. `client` is for tests."""
    body = _document_text(document)
    if not body.strip():
        return []
    if client is None:
        key = api_key()
        if not key:
            raise ReaderUnavailable(
                f"{KEY_NAME} is not set in the engine's environment, so this report cannot be "
                "read. It is written as prose, so there is nothing to read it with by rule.")
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
