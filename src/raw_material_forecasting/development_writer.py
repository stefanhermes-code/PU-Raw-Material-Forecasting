"""Turning a recorded development into the sentence a customer reads.

Stefan, 10 October 2026, on the edition before this one, pointing at three paragraphs in a row:
"China TDI tightens? Why??", "What the f means: a synchronized rebound across the flexible-foam
raw-material basket", "This whole paragraph is a load of shyte. It does not explain anything."

He was right, and the fault was not in the report. The register holds the wording that came in
with the briefings, and that wording analyses instead of observing. He settled how to fix it:
"You need to run it through PU Observatory with clear instructions."

So the register keeps what it was given, and this module asks the Observatory for the customer
sentence. Three rules make that safe to print.

Nothing may be added. Every company, place, number, unit and date in the answer has to appear in
the entry it was written from. An answer that introduces one is refused and the entry falls back
to its own wording, because a sentence nobody can trace is worse than a clumsy one.

Nothing may be advised. The sentence records what happened. It never tells a reader what to do,
what to watch or what to think.

Nothing may be invented to fill a gap. Where the entry does not say why something happened, the
sentence does not say why either. A market that tightened for reasons nobody recorded is a market
that tightened, and the report says that and stops.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from raw_material_forecasting.document_reader import (KEY_NAME, ReaderUnavailable, api_key,
                                                      configured)

DEFAULT_MODEL = "gpt-4.1"
PROJECT = Path(__file__).resolve().parents[2]
CACHE = PROJECT / "data" / "market_intelligence" / "development_wording.json"

INSTRUCTIONS = """You rewrite one recorded market development into the sentences a purchasing
manager reads in a published market report.

You are given what was recorded: a headline, a context note and an implication note. All three
were written by an analyst for internal use. Your job is to turn them into plain observation.

Rules, in order of importance:

1. Add nothing. Every company, country, plant, number, unit, percentage and date in your answer
   must already appear in the material you were given. If it is not there, it may not be written.
2. Advise nothing. Do not tell the reader to track, watch, monitor, consider, expect, keep,
   follow, prepare or review anything. Do not say what a decision should be. The reader decides.
3. Explain only what you were told. If the material does not say why something happened, your
   answer does not say why either. Never supply a cause, a mechanism or a motive of your own.
4. Drop the jargon. No "synchronized rebound", no "raw-material basket", no "read-through", no
   "signal", no "directional", no "margin analysis", no "confirms the direction". Write the way a
   plant manager speaks.
5. Keep it short. One sentence, two at the most. No list. No heading. No date at the start: the
   report prints the date itself.
6. State what was observed, in the past or present tense, with no hedging about evidence and no
   commentary about the report.

Return JSON: {"sentence": "<the sentences>"} and nothing else. If the material holds nothing a
customer could use, return {"sentence": ""}.
"""

# Words that turn a record into advice.
TELLING = ("track", "watch", "monitor", "consider", "recommend", "advise", "keep an eye",
           "prepare", "review", "buy", "sell", "hedge")
# A verb only advises when it is aimed at someone. "Prices should ease" is an expectation;
# "buyers should hedge" is an instruction.
AIMED_AT = ("you ", "your ", "buyers ", "buyer ", "readers ", "reader ", "purchasers ",
            "purchasing managers ", "customers ", "market participants", "participants ",
            "producers should", "converters ", "manufacturers should", "the trade ",
            "we recommend", "it is advisable", "it is recommended", "it is prudent",
            "it would be wise")

# The house style, applied to a sentence the Observatory wrote. The token guard below catches an
# invented fact; this catches the wording Stefan objected to by name, which invents nothing and
# says nothing either.
JARGON = ("synchronized rebound", "raw-material basket", "raw material basket", "read-through",
          "signal", "margin analysis", "headwind", "tailwind", "landscape", "sentiment-driven",
          "going forward", "at the end of the day", "it is important to note")

# Anything that reads as a figure, a date or a proper name has to be traceable to the entry.
TOKEN = re.compile(r"[A-Z][A-Za-z&.'-]+|\d[\d,.]*\s*(?:kt|mt|%|per cent)?", re.U)
SMALL_WORDS = {"The", "A", "An", "In", "On", "At", "For", "From", "To", "By", "With", "And",
               "Of", "It", "Its", "This", "That", "These", "Those", "No", "Not", "Prices",
               "Price", "Supply", "Demand", "Output", "Plant", "Plants", "Producers", "Producer",
               "Buyers", "Buyer", "Market", "Markets", "Offers", "Offer", "Purchases", "Orders"}


class WriterUnavailable(ReaderUnavailable):
    """No key, or the Observatory could not be reached."""


@dataclass(frozen=True)
class Written:
    key: str
    sentence: str
    source: str          # "observatory" or "register"
    headline: str = ""
    why: tuple = ()      # why the register's own line stands, or what to note about the rewrite

    @property
    def rewritten(self) -> bool:
        return self.source == "observatory"


def _material(entry) -> str:
    day = str(getattr(entry, "event_date", "") or getattr(entry, "reported", "") or "").strip()
    written = ""
    if day:
        try:
            from datetime import date as _date

            written = f"{_date.fromisoformat(day[:10]):%d %B %Y}".lstrip("0")
        except ValueError:
            written = day
    parts = [f"Date: {written}" if written else "",
             str(getattr(entry, "headline", "") or "").strip(),
             str(getattr(entry, "context", "") or "").strip(),
             str(getattr(entry, "implication", "") or "").strip()]
    return "\n".join(part for part in parts if part)


def _tokens(text: str) -> set[str]:
    found = set()
    for piece in TOKEN.findall(text or ""):
        piece = piece.strip().rstrip(".,;:")
        if not piece or piece in SMALL_WORDS:
            continue
        found.add(piece.lower().replace(" ", ""))
    return found


def unsupported(sentence: str, material: str) -> list[str]:
    """Anything in the answer that the material does not hold."""
    held = _tokens(material)
    out = []
    for piece in sorted(_tokens(sentence)):
        if piece in held:
            continue
        if any(piece in one or one in piece for one in held):
            continue
        out.append(piece)
    return out


def telling_word(sentence: str) -> str:
    """The word that turns a record into an instruction, or an empty string.

    A verb only advises when it is aimed at someone, or when it opens a clause as a command.
    "Prices should ease" is an expectation. "Buyers should hedge" is an instruction, and so is
    "Track Chinese export offers." standing on its own.
    """
    low = " " + " ".join(str(sentence).lower().split()) + " "
    for phrase in AIMED_AT:
        if f" {phrase}" in low:
            for word in ("should", "must", "need to", "ought", "remain alert", "stay alert",
                         "be aware", "take note") + TELLING:
                if f" {word}" in low:
                    return f"{phrase.strip()} ... {word}"
    for clause in re.split(r"[.;:]\s+", " ".join(str(sentence).split())):
        first = clause.strip().split(" ")[0].lower().strip(",")
        if first in TELLING:
            return first
    return ""


def tells_the_reader_what_to_do(sentence: str) -> bool:
    return bool(telling_word(sentence))


def jargon_word(sentence: str) -> str:
    """The word that tripped the house style, so a finding never makes anyone guess."""
    from raw_material_forecasting.report_gates import CONTRASTIVE

    low = " " + " ".join(str(sentence).lower().split()) + " "
    for word in JARGON:
        if word in low:
            return word
    for form in CONTRASTIVE:
        if form in low:
            return form.strip()
    return ""


def uses_jargon(sentence: str) -> bool:
    return bool(jargon_word(sentence))


def judge(sentence: str, material: str) -> tuple[str, list[str]]:
    """The sentence as it may be printed, and whatever is worth saying about it."""
    said = " ".join(str(sentence or "").split())
    if not said:
        return "", ["the Observatory returned nothing for it"]
    aimed = telling_word(said)
    if aimed:
        return "", [f'it reads as an instruction to the reader ("{aimed}")']
    missing = unsupported(said, material)
    if missing:
        return "", [f"it states something the register does not: {', '.join(missing[:4])}"]
    notes = []
    word = jargon_word(said)
    if word and word not in material.lower():
        notes.append(f'house style: "{word}"')
    return said, notes


def accept(sentence: str, material: str) -> str:
    """The sentence if it may be printed, and an empty string if it may not."""
    return judge(sentence, material)[0]


def key_for(entry) -> str:
    return "|".join([str(getattr(entry, "reported", "") or ""),
                     str(getattr(entry, "event_date", "") or ""),
                     str(getattr(entry, "headline", "") or "")])[:400]


def load_cache(path: Path | None = None) -> dict[str, str]:
    where = Path(path) if path else CACHE
    try:
        return dict(json.loads(where.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def save_cache(held: dict[str, str], path: Path | None = None) -> None:
    where = Path(path) if path else CACHE
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(held, indent=2, sort_keys=True, ensure_ascii=True),
                     encoding="utf-8")


def _over_https(material: str, *, model: str, key: str) -> str:
    """The same request without the client library."""
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": INSTRUCTIONS},
                     {"role": "user", "content": material}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            held = json.loads(answer.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as problem:
        detail = problem.read().decode("utf-8", "ignore")[:200]
        raise WriterUnavailable(f"{problem.code} from the Observatory: {detail}") from None
    except Exception as problem:
        raise WriterUnavailable(f"the Observatory could not be reached: {problem}") from None
    text = (((held.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
    try:
        return str((json.loads(text) or {}).get("sentence") or "")
    except ValueError:
        return ""


def ask(material: str, *, model: str = DEFAULT_MODEL, client=None) -> str:
    """One development to the Observatory, and the sentence it sends back."""
    if not material.strip():
        return ""
    if client is None:
        key = api_key()
        if not key:
            raise WriterUnavailable(
                f"{KEY_NAME} is not set in the engine's environment, so the recorded wording "
                "cannot be rewritten.")
        try:
            from openai import OpenAI
        except ModuleNotFoundError:
            return _over_https(material, model=model, key=key)
        client = OpenAI(api_key=key)
    try:
        answer = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, temperature=0,
            messages=[{"role": "system", "content": INSTRUCTIONS},
                      {"role": "user", "content": material}])
        text = answer.choices[0].message.content or "{}"
    except WriterUnavailable:
        raise
    except Exception as exc:
        raise WriterUnavailable(f"the Observatory could not be reached: {exc}") from exc
    try:
        return str((json.loads(text) or {}).get("sentence") or "")
    except ValueError:
        return ""


def write(entry, *, cache: dict[str, str] | None = None, model: str = DEFAULT_MODEL,
          client=None) -> Written:
    """The customer sentence for one development, from the cache where it is already settled."""
    key = key_for(entry)
    held = cache if cache is not None else load_cache()
    material = _material(entry)
    headline = str(getattr(entry, "headline", "") or "")[:70]
    if key in held:
        return Written(key=key, sentence=held[key], source="observatory", headline=headline)
    if client is None and not configured():
        return Written(key=key, sentence="", source="register", headline=headline,
                       why=("the Observatory is not reachable from this machine",))
    said, notes = judge(ask(material, model=model, client=client), material)
    if not said:
        return Written(key=key, sentence="", source="register", headline=headline,
                       why=tuple(notes))
    held[key] = said
    return Written(key=key, sentence=said, source="observatory", headline=headline,
                   why=tuple(notes))


def write_all(entries: Iterable[Any], *, path: Path | None = None, model: str = DEFAULT_MODEL,
              client=None) -> dict[str, Written]:
    """Every development, written once and kept, so an edition costs nothing to rebuild."""
    held = load_cache(path)
    out: dict[str, Written] = {}
    before = dict(held)
    for entry in entries:
        out[key_for(entry)] = write(entry, cache=held, model=model, client=client)
    if held != before:
        save_cache(held, path)
    return out
