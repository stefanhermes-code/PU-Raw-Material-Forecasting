"""The gates a finished edition has to pass before anyone sees it.

Stefan, 10 October 2026, after setting our report against the one Charlie's engine wrote: the
report itself is a symptom, so fix the way the report is made. The production standard he and I
settled on carries hard gates, and a gate written only in prose is a wish. This module is the gate.

Every check here reads the rendered document, not the code that built it. A rule that holds in the
builder and fails in the Word file is still a failure, and the only text that matters is the text
a customer sees - headings, tables, captions, headers and footers included.

Nothing here repairs a document. A failed gate is reported and the edition is built again.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, Sequence

from raw_material_forecasting.intelligence_register import (describes_its_sourcing,
                                                            names_a_source)

# Words that disclose how the document was made. Stefan, 9 October 2026: "no meta leaking,
# disclosing of methods, etc. Show confidence."
BANNED = (
    "the first reading", "covered in this edition", "movement measured across it",
    "evidence status", " recorded", "graded", "methodology", "backfill",
    "confirmed by a second", "validated", "our collection", "our data", "data gap",
    "as reported by", "according to", "confidence score", "model accuracy",
)

# "X rather than Y" and its relatives. Stefan: say what is the case.
CONTRASTIVE = (" rather than ", " instead of ", " is not ", " are not ", " was not ", " not a ")

# Phrases that place one of our own figures in time. Each one has to match a scope label the
# facts supplied.
#
# Only two kinds count. A vague period word - "over the period", "during the quarter" - always
# does, because it claims an extent without naming one. And this edition's own period name does,
# because that is the claim the toluene sentence made falsely. A date inside a development's own
# wording, "cut its Omuta plant in July 2025", states when something happened in the world and
# scopes nothing of ours, so it is left alone.
FIGURE = re.compile(r"(?<![\w/.-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)(?![\w/-])")
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November"
          "|December")
DATE = re.compile(
    r"\b\d{1,2}\s+(?:" + MONTHS + r")\s+\d{4}\b"
    r"|\b(?:" + MONTHS + r")\s+\d{4}\b"
    r"|\bQ[1-4]\s+\d{4}\b"
    r"|\b(?:19|20)\d{2}\b", re.I)
NOT_A_FIGURE = DATE


def _plain(figure: str) -> str:
    """One spelling for one number, so 2,642 and 2642.0 are the same figure."""
    held = str(figure).replace(",", "").strip()
    if held.endswith(".0"):
        held = held[:-2]
    return held.lstrip("0") or "0"


def figures_in(line: str) -> set[str]:
    """Every figure a sentence states, with the dates taken out of it first."""
    return {_plain(one) for one in FIGURE.findall(DATE.sub(" ", str(line)))}


PREPOSITION = re.compile(
    r"\b(?:over|during|across|within|through|throughout|in|for|on)\s+(?:the\s+|this\s+)?", re.I)


# Words that set one market's movement beside another's. Charlie, 10 October 2026: "These
# movements can be discussed together, but their percentages cannot be presented as directly
# comparable quarterly movements. The validation must check the actual observation windows,
# including cross-market comparisons. The narrative should retain the freedom to discuss
# meaningful relationships between markets."
#
# So the relationship is never the objection. Equating the two windows is. A sentence that names
# the shorter window itself passes, which is the sentence we want written.
TOGETHER = re.compile(
    r"\b(?:together|in tandem|tandem|synchroni[sz]\w*|synchronous\w*|in step|lockstep"
    r"|in parallel|parallel|mirror\w*|alike|likewise|simultaneous\w*|in line with"
    r"|comparable|similarly|similar|both|while|whereas|versus|compared|against"
    r"|tracked|tracking|outpac\w*|lagg\w*)\b", re.I)


def _named_in(line: str, rows: Sequence[dict]) -> list[dict]:
    """The markets a sentence speaks about: the ones it names and the ones it quotes."""
    found = figures_in(line)
    out, seen = [], set()
    for row in rows:
        code = str(row.get("chemical") or "").strip()
        by_name = bool(code) and re.search(rf"\b{re.escape(code)}\b", line, re.I) is not None
        if not by_name and not (_figures_of(row) & found):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append(row)
    return out


def _window_of(row: dict) -> tuple[str, str]:
    return (str(row.get("first_observation") or ""), str(row.get("last_observation") or ""))


def check_comparisons(said: Sequence[str], *, facts: dict | None = None) -> list[str]:
    """Whether two markets compared in one sentence were read over the same stretch of days."""
    rows = _rows_of(facts or {})
    out: list[str] = []
    for whole in said:
        for line in _sentences(whole) or [whole]:
            if not TOGETHER.search(line):
                continue
            here = _named_in(line, rows)
            if len(here) < 2:
                continue
            windows = {_window_of(row) for row in here}
            bands = {str(row.get("band", "")).upper() for row in here}
            if len(windows) < 2 or bands == {"FULL"}:
                continue
            short = [row for row in here if str(row.get("band", "")).upper() != "FULL"]
            if not short:
                continue
            low = line.lower()
            if any(str(row.get("scope_label") or "").strip().lower() in low for row in short):
                continue
            said_as = ", ".join(
                f"{row.get('chemical') or row.get('market')} {row.get('scope_label') or 'over part of the period'}"
                for row in here)
            out.append(f"SCOPE: compared as one movement where the windows differ - {said_as} - "
                       f"in: {line[:70]}")
    return out


def _sentences(line: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]


def _rows_of(facts: dict) -> list[dict]:
    out = []
    for key in ("markets", "feedstocks"):
        for row in (facts.get(key) or []):
            if isinstance(row, dict):
                out.append(row)
    return out


def _figures_of(row: dict) -> set[str]:
    """The figures that belong to one market and to no other."""
    out: set[str] = set()
    for field in ("open", "close", "low", "high", "move_absolute", "move_percent"):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Signed and unsigned: a fall is supplied as -1.9 and written as "declined 1.9".
            for held in (value, abs(value)):
                out.add(_plain(f"{held:,.0f}"))
                out.add(_plain(f"{held:,.1f}"))
    return out


def check_scope(said: Sequence[str], *, facts: dict | None = None,
                labels: Sequence[str] = (), period_name: str = "") -> list[str]:
    """Whether a figure is placed in a window the evidence supports.

    Three things changed on 10 October 2026, after the first Observatory draft. A sentence that
    carries no figure is left alone, because Charlie's instruction says a dated development may
    be described in normal prose and a period named there scopes nothing. A market read end to
    end may be written "in Q3 2026" as well as "over Q3 2026", because both make the same claim
    and the preposition was never the point. And a market read over part of the period may not be
    placed in the period at all, which is the toluene sentence and the reason any of this exists.
    """
    allowed = {str(one).strip().lower() for one in labels if str(one).strip()}
    rows = _rows_of(facts or {})
    named = str(period_name).strip()
    out: list[str] = []
    for whole in said:
        for line in _sentences(whole) or [whole]:
            found = figures_in(line)
            if not found:
                continue
            here = [row for row in rows if _figures_of(row) & found]
            partial = [row for row in here if str(row.get("band", "")).upper() != "FULL"]
            for match in re.finditer(
                    PREPOSITION.pattern + re.escape(named) if named else PREPOSITION.pattern,
                    line, re.I):
                phrase = " ".join(match.group(0).split()).lower()
                if phrase in allowed or any(phrase in one for one in allowed):
                    continue
                if partial:
                    row = partial[0]
                    out.append(f"SCOPE: \"{match.group(0).strip()}\" places a figure read "
                               f"{row.get('scope_label', 'over part of the period')} in the whole "
                               f"period, in: {line[:70]}")
                elif not here:
                    out.append(f"SCOPE: \"{match.group(0).strip()}\" carries a figure no market "
                               f"claims, in: {line[:70]}")
            if partial and not re.search(PREPOSITION.pattern + re.escape(named), line, re.I) \
                    and named and named.lower() in line.lower():
                row = partial[0]
                out.append(f"SCOPE: \"{named}\" names the whole period for a figure read "
                           f"{row.get('scope_label', 'over part of it')}, in: {line[:70]}")
    out += check_comparisons(said, facts=facts)
    return out


TERMINAL = ".!?:;\u2026"


@dataclass(frozen=True)
class Failure:
    gate: str
    detail: str

    def __str__(self) -> str:
        return f"{self.gate}: {self.detail}"


def document_text(document: bytes) -> list[str]:
    """Every run of text in the file, in reading order, tables and furniture included."""
    out: list[str] = []
    with zipfile.ZipFile(BytesIO(document)) as book:
        names = [name for name in book.namelist()
                 if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", name)]
        for name in sorted(names):
            xml = book.read(name).decode("utf-8", "ignore")
            for block in re.split(r"</w:p>", xml):
                text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", block))
                text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                if text.strip():
                    out.append(text.strip())
    return out


def check_sources(lines: Sequence[str]) -> list[Failure]:
    """No assessor, desk, index or vendor is named anywhere, in any form.

    A sentence can leak the sourcing without naming anybody - "the earlier source set showed 143
    transits", "no named producer in the report" - and both of those were on the Market
    Intelligence page on 6 November 2026. `describes_its_sourcing` is that shape, tested on its
    own patterns rather than on whether `source_free` would rewrite the line: that function also
    tidies whitespace, and the first version of this gate failed a clean report on its own footer.
    """
    out = []
    for whole in lines:
        for line in _sentences(whole) or [whole]:
            if names_a_source(line):
                out.append(Failure("SOURCE", f'a source is named, in: "{line.strip()}"'))
            elif describes_its_sourcing(line):
                out.append(Failure("SOURCE",
                                   f'the sourcing is described, in: "{line.strip()}"'))
    return out


def check_method(lines: Sequence[str]) -> list[Failure]:
    out = []
    for whole in lines:
        for line in _sentences(whole) or [whole]:
            low = line.lower()
            for word in BANNED:
                if word in low:
                    out.append(Failure("METHOD", f'"{word}", in: "{line.strip()}"'))
    return out


def check_contrastive(lines: Sequence[str]) -> list[Failure]:
    out = []
    for line in lines:
        low = " " + line.lower() + " "
        for form in CONTRASTIVE:
            if form in low:
                out.append(Failure("WRITING", f'contrastive "{form.strip()}" in: {line[:90]}'))
    return out


# What a joint conclusion may not do. Charlie's CR-06, 11 October 2026: the report draws two
# materials together as a market assessment and never as arithmetic. "Do not calculate or estimate
# their combined contribution to total foam manufacturing costs", no aggregate cost percentage, no
# formulation weighting, no consumption ratio, no manufacturing-margin impact.
#
# A phrase alone is not the fault: "the combined cost pressure is higher" is an assessment and
# belongs there. A figure attached to one is the fault, because that is the calculation. So a
# finding is raised only where the sentence carries both.
COMBINED_COST = (
    "combined cost", "combined raw material cost", "combined raw-material cost",
    "combined price", "combined forecast", "combined contribution",
    "total cost", "total manufacturing cost", "total raw material cost",
    "total raw-material cost", "aggregate cost", "aggregate price", "aggregate forecast",
    "blended cost", "blended price", "weighted average", "weighting",
    "consumption ratio", "formulation ratio", "cost weighting",
    "per cent of total", "per cent of the total", "share of total cost",
    "margin impact", "impact on margin", "cost per kilogram", "cost per kg",
)


def check_combined_cost(lines: Sequence[str]) -> list[Failure]:
    """A combined manufacturing cost, put as a number. Never published, by CR-06."""
    out = []
    for whole in lines:
        for line in _sentences(whole) or [whole]:
            if not figures_in(line):
                continue
            low = " " + " ".join(line.lower().split()) + " "
            for phrase in COMBINED_COST:
                if phrase in low:
                    out.append(Failure("EXCLUDED", f'"{phrase}" put as a figure, in: '
                                                   f'"{line.strip()}"'))
                    break
    return out


def _ends_in_a_list_of_names(line: str) -> bool:
    """A title or heading that lists places: "Adipic Acid - China, Europe, India and Japan".

    Its commas belong to the list of names, not to a sentence part way through. Stefan's adipic
    acid report of 14 October 2026 carried five places in its title and the gate read it as an
    unfinished sentence. The last word must be a name; a list ending in "and" is still cut off.
    """
    words = line.replace(",", " , ").split()
    if not words or not words[-1][:1].isupper():
        return False
    at = len(words)
    while at > 0 and (words[at - 1][:1].isupper() or words[at - 1] in {",", "and", "&"}):
        at -= 1
    rest = words[:at]
    return "," not in rest and ";" not in " ".join(rest)


def check_truncation(lines: Sequence[str]) -> list[Failure]:
    """A sentence that stops without stopping. Nothing is ever cut to make a box fit."""
    out = []
    for line in lines:
        # A heading or a strip of furniture carries no full stop and needs none. A line that
        # punctuates itself part way through is a sentence, and a sentence has to finish.
        if len(line.split()) < 8 or not ("," in line or ";" in line):
            continue
        if line[-1] in TERMINAL or line[-1].isdigit() or line[-1] in "%)\"'":
            continue
        if _ends_in_a_list_of_names(line):
            continue
        out.append(Failure("TRUNCATION", f"no end to: ...{line[-70:]}"))
    return out


def check_repetition(lines: Sequence[str]) -> list[Failure]:
    """The same statement twice in one edition, in the same words."""
    seen: dict[str, int] = {}
    out = []
    for line in lines:
        for sentence in _sentences(line):
            if len(sentence.split()) < 8:
                continue
            key = re.sub(r"[^a-z0-9 ]", "", sentence.lower())
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                out.append(Failure("WRITING", f"said twice: {sentence[:90]}"))
    return out


def check_numbers(lines: Sequence[str], allowed: Iterable[str]) -> list[Failure]:
    """Every figure on the page traced to one the facts produced.

    Only figures that read as money, a percentage or a quantity are checked. Dates, page numbers
    and the year inside a period name are not figures of this kind.
    """
    known = {str(value).strip() for value in allowed if str(value).strip()}
    out = []
    for line in lines:
        for found in re.findall(r"(?<![\w/.-])(\d{1,3}(?:,\d{3})+|\d+\.\d+)(?![\w/-])", line):
            if found in known:
                continue
            if found.replace(",", "") in {value.replace(",", "") for value in known}:
                continue
            out.append(Failure("NUMBER", f"{found} was not supplied, in: {line[:90]}"))
    return out


def run(document: bytes, *, labels: Iterable[str] = (), numbers: Iterable[str] = (),
        exempt: Iterable[str] = (), carried: Iterable[str] = (), period_name: str = "",
        facts: dict | None = None, check_figures: bool = True) -> list[Failure]:
    """Every gate, over the rendered file. An empty list is the only pass.

    `exempt` holds the standing notices. A legal notice says "are not an offer" because that is
    what it has to say, and reading it as market prose produces a failure nobody can act on.
    """
    lines = document_text(document)
    spared = {" ".join(str(one).split()) for one in exempt if str(one).strip()}
    quoted = [" ".join(str(one).split()) for one in carried
               if len(" ".join(str(one).split())) >= 40]
    prose_lines = [line for line in lines if " ".join(line.split()) not in spared]
    # Text carried in from a document HTC Global already published is evidence. It is read for a
    # source name, for method and for an unfinished sentence, and its house style is left as its
    # author wrote it, because editing evidence to suit a style rule is editing evidence.
    def ours(line: str) -> bool:
        tidy = " ".join(line.split())
        return not any(one in tidy for one in quoted)

    written = [line for line in prose_lines if ours(line)]
    out: list[Failure] = []
    out += check_sources(lines)
    out += check_method(prose_lines)
    out += [Failure("SCOPE", one.split(":", 1)[1].strip())
            for one in check_scope(prose_lines, facts=facts, labels=labels,
                                   period_name=period_name)]
    out += check_truncation(prose_lines)
    out += check_combined_cost(prose_lines)
    # House style is no longer gated here. Charlie, 10 October 2026: "Numerical integrity,
    # temporal consistency, missing factual support, JSON structure and incomplete sentences
    # should remain subject to strict validation. Editorial language should be assessed through
    # the narrative instruction and Stefan's final review. We should not have arbitrary wording
    # checks prevent an otherwise valid market analysis." `check_contrastive` and
    # `check_repetition` are kept for anyone reading a draft on purpose; the gates do not run
    # them, and `written` is left here because that is what they would be read over.
    _house_style_is_the_editors = written
    if check_figures:
        out += check_numbers(lines, numbers)
    return out


def labels_of(markets: Sequence, feedstocks: Sequence = ()) -> list[str]:
    """The scope labels an edition is allowed to use, taken off the rows themselves."""
    out: list[str] = []
    for row in list(markets) + list(feedstocks):
        for attribute in ("scope_label", "comparison_label", "window_label"):
            value = getattr(row, attribute, "")
            if value:
                out.append(str(value))
    return sorted(set(out))


# The figures on a chart against the figures in the text, before anyone sees either.
#
# Stefan, 11 October 2026: "Any discrepancy between displayed chart figures and narrative figures
# must stop report release and produce a diagnostic identifying the material, field and conflicting
# values." The discrepancy that prompted it had a cause - two different identity lists - and that
# cause is fixed. This is the guard that says so every time, because the next cause will be a
# different one and nobody should find it by reading a chart.
def check_chart_agreement(series: dict, facts: dict | None = None,
                          rows: Sequence = ()) -> list[Failure]:
    """Each market's drawn line against the figures written about it.

    `series` maps "CHEMICAL|Geography" to the (date, value) points the chart was drawn from.
    `facts` is the pack the narrative was given; `rows` are the MarketPeriod rows, read where the
    pack does not carry a market.
    """
    held: dict[str, dict] = {}
    for row in _rows_of(facts or {}):
        key = f"{row.get('chemical')}|{row.get('geography')}"
        held[key] = row
    for row in rows or ():
        key = f"{getattr(row, 'chemical', '')}|{getattr(row, 'geography', '')}"
        held.setdefault(key, {
            "open": getattr(row, "opened", None), "close": getattr(row, "closed", None),
            "high": getattr(row, "high", None), "low": getattr(row, "low", None),
            "first_observation": str(getattr(row, "opened_on", ""))[:10],
            "last_observation": str(getattr(row, "closed_on", ""))[:10],
            "market": f"{getattr(row, 'chemical', '')} {getattr(row, 'geography', '')}"})
    out: list[Failure] = []
    for key, points in (series or {}).items():
        row = held.get(key)
        if not row or not points:
            continue
        drawn = sorted((str(day)[:10], float(value)) for day, value in points)
        values = [value for _day, value in drawn]
        named = str(row.get("market") or key.replace("|", " "))
        for field, chart_said, text_said in (
                ("opening price", drawn[0][1], row.get("open")),
                ("closing price", drawn[-1][1], row.get("close")),
                ("high", max(values), row.get("high")),
                ("low", min(values), row.get("low")),
                ("first observation", drawn[0][0], row.get("first_observation")),
                ("last observation", drawn[-1][0], row.get("last_observation"))):
            if text_said is None or text_said == "":
                continue
            if isinstance(text_said, (int, float)):
                agrees = round(float(chart_said)) == round(float(text_said))
                chart_shown, text_shown = f"{chart_said:,.0f}", f"{float(text_said):,.0f}"
            else:
                agrees = str(chart_said)[:10] == str(text_said)[:10]
                chart_shown, text_shown = str(chart_said)[:10], str(text_said)[:10]
            if not agrees:
                out.append(Failure("DISAGREEMENT",
                                   f"{named}: the chart draws a {field} of {chart_shown} and the "
                                   f"text states {text_shown}"))
    return out
