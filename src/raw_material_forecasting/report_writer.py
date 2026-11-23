"""The narrative of an edition, written by the PU Observatory engine.

Stefan, 10 October 2026, settling who does what: "the Application owns the data, the period, the
chemical, the forecast, etc. But the narrative should be done by the PU Observatory Engine, with
the market intelligence that you have PLUS any other intelligence the PU Observatory Engine can
add from within the period."

So the line runs through the number. Every figure in the finished document comes from the fact
pack this application computed, with the words that scope it already decided. The engine is given
those figures, the standard they have to be presented under, and everything HTC Global recorded
around the market in the period. It writes the sentences, and it may bring an observation of its
own as long as that observation falls inside the period and carries no figure.

What comes back is a draft, not a document. It goes through `report_gates` against the same rules
the deterministic text goes through, and a draft that fails is refused and asked for again. A
second failure ends it: the deterministic sentences are already correct and already pass, so the
edition falls back to them rather than printing something nobody checked.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from raw_material_forecasting.document_reader import KEY_NAME, api_key, configured
from raw_material_forecasting.development_writer import WriterUnavailable
from raw_material_forecasting import report_standard
from raw_material_forecasting.report_gates import (DATE, FIGURE, _plain, check_scope,
                                                   figures_in)

DEFAULT_MODEL = "gpt-4.1"

# The sections the engine writes. Everything else in the document is a figure, a table or a
# picture, and none of those are its to make.
SECTIONS = ("headline", "opening", "observed", "background", "route", "expectation")

# The instruction that used to live here was mine. It is the editor's now, written by Charlie
# on 10 October 2026, and it is read from `data/config/report_narrative_instruction.md` so that
# changing how the report reads never means changing code.
FALLBACK_ASKING = """You write the narrative of a polyurethane raw-material market report.
Return one JSON object with exactly these keys and no others: headline, opening, observed,
background, route, expectation. headline, opening, route and expectation are strings; observed
and background are objects keyed by the market names in the fact pack. Use only numbers that
appear in the fact pack, character for character, and only the scope labels it supplies. Name no
source. Return nothing but the JSON."""


def asking(path=None) -> str:
    """Block one. The editor's instruction where there is one."""
    held = report_standard.narrative_instruction(path)
    return held.strip() or FALLBACK_ASKING




class NarrativeRefused(RuntimeError):
    """Kept so a caller written against the old behaviour still imports. Nothing raises it now."""


# What a finding is about. Stefan, 10 October 2026: "You are the engineer, Charlie is the
# salesperson. So on the narrative he is editorial freedom. You cannot block his narrative. At
# the end of the day I am the chief editor and it is my name on the report."
#
# So nothing here discards a draft. A finding is reported and the chief editor decides. They are
# split because the two kinds are not the same question: one is whether a figure is true, which
# is mine to check; the other is how it reads, which is not mine to settle.
# What is still checked strictly, and so what the editor is shown. Charlie, 10 October 2026:
# "Numerical integrity, temporal consistency, missing factual support, JSON structure and
# incomplete sentences should remain subject to strict validation. Editorial language should be
# assessed through the narrative instruction and Stefan's final review."
#
# House style left the gates with that. Advisory writing is the one editorial fault the engine is
# still asked about, because Charlie asked for it to stop rather than to be flagged - so it is
# put back to the engine once, quietly, and never reaches the page.
FACTUAL = ("NUMBER", "SCOPE", "SOURCE", "METHOD", "TRUNCATION", "EXCLUDED")


@dataclass
class Narrative:
    headline: str = ""
    opening: str = ""
    observed: dict[str, str] = field(default_factory=dict)
    background: dict[str, str] = field(default_factory=dict)
    # A string where the edition covers one material, an object keyed by market where it covers
    # more than one. Two materials cannot share one paragraph about how they are made.
    route: Any = ""
    expectation: str = ""
    attempts: int = 0
    findings: list[str] = field(default_factory=list)

    @property
    def written(self) -> bool:
        return bool(self.headline or self.opening or self.observed)

    @property
    def factual(self) -> list[str]:
        """Findings about whether a figure is ours. These are the engineer's to raise."""
        return [one for one in self.findings if str(one).split(":")[0].strip() in FACTUAL]

    @property
    def editorial(self) -> list[str]:
        """Kept so a caller written against the old split still works. Nothing lands here now."""
        return [one for one in self.findings if str(one).split(":")[0].strip() not in FACTUAL]

    def places(self) -> list[tuple[str, str]]:
        """Every paragraph with the place in the report it was written for.

        Stefan, 11 October 2026, on a finding that read "NUMBER: 26.9 was not supplied, in TDI
        prices in China rose by 11.6 per cent over Q3 2026, closing at USD" - the figure it names
        is not in the words it quotes, so the editor is sent looking. A finding says where it is
        and shows the sentence it is in.
        """
        out = [("the headline", self.headline),
               ("the opening", self.opening)]
        out += [(f"what was observed in {name}", said)
                for name, said in self.observed.items()]
        out += [(f"the market behind {name}", said)
                for name, said in self.background.items()]
        if isinstance(self.route, dict):
            out += [(f"how {name} is made", said) for name, said in self.route.items()]
        else:
            out.append(("the production route", self.route))
        out.append(("what is expected next", self.expectation))
        return [(where, " ".join(str(said).split()))
                for where, said in out if str(said).strip()]

    def lines(self) -> list[str]:
        return [said for _where, said in self.places()]


def material(*, facts: dict, intelligence: dict, standard: str = "") -> str:
    """Block two and block three, in the order the instruction names them.

    Block one is the instruction itself and travels separately, ahead of this, so it arrives
    first and arrives whole. `standard` is kept for a caller that wants to append something of
    its own; nothing in the application passes it.
    """
    out = ["BLOCK 2: QUANTITATIVE FACT PACK",
           json.dumps(facts, indent=2, sort_keys=True, default=str),
           "BLOCK 3: MARKET INTELLIGENCE",
           json.dumps(intelligence, indent=2, sort_keys=True, default=str)]
    if standard.strip():
        out = [standard] + out
    return "\n\n".join(out)


# What counts as a figure in a sentence. A comma-grouped number, a decimal, or a run of digits
# standing on its own. Ordinary words about direction, tightness or availability are not figures
# and are never examined here.
def _figures(value, out: set[str]) -> None:
    """Every figure the material holds, however deeply it is buried in it.

    Charlie, 10 October 2026: "JC's character-for-character numerical validation must distinguish
    numeric content from incidental language." He was right about something sharper than that as
    well. A figure the engine is allowed to use may sit inside a recorded development rather than
    in the fact pack, and reading only the pack called those invented. Both blocks are read, and
    a number inside a sentence counts the same as a number in a field.
    """
    if isinstance(value, dict):
        for one in value.values():
            _figures(one, out)
    elif isinstance(value, (list, tuple, set)):
        for one in value:
            _figures(one, out)
    elif isinstance(value, bool):
        return
    elif isinstance(value, (int, float)):
        # Both spellings of a movement. The pack supplies -1.9 and the sentence writes
        # "declined 1.9 per cent", because the direction belongs in words rather than in a sign.
        # Holding only the signed form called a correct figure invented.
        for held in (value, abs(value)):
            out.add(_plain(f"{held:,.0f}"))
            out.add(_plain(f"{held:,.1f}"))
            out.add(_plain(str(held)))
    elif isinstance(value, str):
        for found in FIGURE.findall(value):
            out.add(_plain(found))


def supplied_figures(*blocks) -> set[str]:
    out: set[str] = set()
    for block in blocks:
        _figures(block, out)
    return out






def check(draft: Narrative, *, facts: dict, labels: Sequence[str], period_name: str,
          material_text: str = "", intelligence: dict | None = None) -> list[str]:
    """Everything wrong with the draft, in the words the gates use."""
    from raw_material_forecasting import report_gates as gates

    said = draft.lines()
    sentences = [line for where, line in draft.places() if where != "the headline"]
    out = [str(one) for one in (
        gates.check_sources(said) + gates.check_method(said)
        + gates.check_truncation(sentences) + gates.check_combined_cost(said))]
    out += check_scope(said, facts=facts, labels=labels, period_name=period_name)
    # Only figures are traced. A name or an observation the engine brings is allowed by the
    # instruction; a figure nobody supplied is the one thing nobody can check.
    held = supplied_figures(facts, intelligence or {})
    for where, line in draft.places():
        for sentence in gates._sentences(line) or [line]:
            for found in FIGURE.findall(DATE.sub(" ", sentence)):
                if _plain(found) in held:
                    continue
                out.append(f"NUMBER: {found} is not a figure the application supplied. "
                           f"It is in {where}: \"{sentence.strip()}\"")
    return out


def advisory(draft: Narrative) -> list[str]:
    """Sentences that instruct the reader instead of assessing the market.

    Charlie, 10 October 2026, on the closing sentence of the forecast: "The report should explain
    expected market conditions, relevant exposure and the factors likely to influence future price
    development. Please have the narrative engine produce a market assessment rather than
    instructions to the reader."

    So this is not shown to the editor and does not hold anything back. It is the one thing the
    draft is handed back for, once.
    """
    from raw_material_forecasting import development_writer as style

    out = []
    for line in draft.lines():
        aimed = style.telling_word(line)
        if aimed:
            out.append(f'ADVICE: "{aimed}" instructs the reader. Write what the market is '
                       f'expected to do and what it exposes, in: {line[:70]}')
    return out


def _parse(text: str) -> Narrative:
    try:
        held = json.loads(text or "{}") or {}
    except ValueError:
        return Narrative()
    return Narrative(
        headline=str(held.get("headline") or "").strip(),
        opening=str(held.get("opening") or "").strip(),
        observed={str(k): str(v).strip() for k, v in (held.get("observed") or {}).items() if v},
        background={str(k): str(v).strip() for k, v in (held.get("background") or {}).items() if v},
        route=({str(k): str(v).strip() for k, v in held["route"].items() if v}
               if isinstance(held.get("route"), dict) else str(held.get("route") or "").strip()),
        expectation=str(held.get("expectation") or "").strip())


def _ask(body: str, *, model: str, client) -> str:
    if client is None:
        key = api_key()
        if not key:
            raise WriterUnavailable(f"{KEY_NAME} is not set in the engine's environment.")
        try:
            from openai import OpenAI
        except ModuleNotFoundError:
            return _over_https_narrative(body, model=model, key=key)
        client = OpenAI(api_key=key)
    try:
        answer = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, temperature=0,
            messages=[{"role": "system", "content": asking()},
                      {"role": "user", "content": body}])
        return answer.choices[0].message.content or "{}"
    except WriterUnavailable:
        raise
    except Exception as problem:
        raise WriterUnavailable(f"the Observatory could not be reached: {problem}") from None


def _over_https_narrative(body: str, *, model: str, key: str) -> str:
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": asking()},
                     {"role": "user", "content": body}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=payload, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as answer:
            held = json.loads(answer.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as problem:
        detail = problem.read().decode("utf-8", "ignore")[:200]
        raise WriterUnavailable(f"{problem.code} from the Observatory: {detail}") from None
    except Exception as problem:
        raise WriterUnavailable(f"the Observatory could not be reached: {problem}") from None
    return (((held.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"


def write(*, facts: dict, intelligence: dict, labels: Sequence[str], period_name: str,
          standard: str | None = None, model: str = DEFAULT_MODEL, client=None,
          tries: int = 2) -> Narrative:
    """The narrative, or an empty one where it could not be made to meet the standard."""
    if client is None and not configured():
        raise WriterUnavailable(f"{KEY_NAME} is not set in the engine's environment.")
    body = material(facts=facts, intelligence=intelligence, standard=standard or "")
    held: list[str] = []
    draft = Narrative()
    for attempt in range(1, max(1, tries) + 1):
        asked = body if attempt == 1 else (
            body + "\n\nYOUR PREVIOUS DRAFT RAISED THESE POINTS. Where a figure or a date is "
            "wrong, correct it. The rest is yours to settle as you see fit.\n" + "\n".join(held))
        draft = _parse(_ask(asked, model=model, client=client))
        draft.attempts = attempt
        if not draft.written:
            held = ["the draft came back empty"]
            continue
        found = check(draft, facts=facts, labels=labels, period_name=period_name,
                      intelligence=intelligence)
        draft.findings = list(found)
        # One chance to correct a figure, a window or an instruction to the reader. Everything
        # else goes to the chief editor exactly as it was written.
        held = found + advisory(draft)
        if not held:
            return draft
    return draft
