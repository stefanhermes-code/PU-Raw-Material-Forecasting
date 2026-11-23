"""Reading the finished publication back, before anyone is offered it.

V2.1 section 10 makes this a release control rather than a courtesy: "Any discrepancy blocks
release." So every figure printed in the document is traced to the fact pack, and every scope
word is checked against the window it describes.

Stefan, 12 October 2026: there is one report and it is the Word file, which he approves and saves
as PDF for the customer. So the check reads the lines of the document that is actually issued.
The PDF reader stays here because a PDF put in front of us can still be read back the same way.
"""
from __future__ import annotations

from typing import Any, Sequence

from raw_material_forecasting.report_gates import (DATE, FIGURE, _plain, check_combined_cost,
                                                   check_method, check_scope, check_sources,
                                                   check_truncation, labels_of)


def text_of(document: bytes) -> list[str]:
    """Every line of text in the PDF, in reading order."""
    from io import BytesIO

    from pypdf import PdfReader

    out: list[str] = []
    reader = PdfReader(BytesIO(document))
    for page in reader.pages:
        for line in (page.extract_text() or "").splitlines():
            if line.strip():
                out.append(" ".join(line.split()))
    return out


def pages(document: bytes) -> int:
    from io import BytesIO

    from pypdf import PdfReader

    return len(PdfReader(BytesIO(document)).pages)


def is_searchable(document: bytes) -> bool:
    """Selectable text, which section 9 requires by name."""
    return len(" ".join(text_of(document))) > 200


def check(document: bytes, *, facts: dict, supplied: Sequence[str] = (),
          labels: Sequence[str] = (), period_name: str = "",
          exempt: Sequence[str] = ()) -> list[str]:
    """Everything wrong with a finished PDF, in the words the gates use."""
    out = check_lines(text_of(document), facts=facts, supplied=supplied, labels=labels,
                      period_name=period_name, exempt=exempt)
    if not is_searchable(document):
        out.append("RENDER: the publication carries no selectable text.")
    return out


def check_lines(lines: Sequence[str], *, facts: dict, supplied: Sequence[str] = (),
                labels: Sequence[str] = (), period_name: str = "",
                exempt: Sequence[str] = ()) -> list[str]:
    """Everything wrong with the finished document, read off its own text."""
    spared = {" ".join(str(one).split()) for one in exempt if str(one).strip()}
    said = [one for one in lines if one not in spared]
    out = [str(one) for one in (check_sources(said) + check_method(said)
                                + check_truncation(said) + check_combined_cost(said))]
    out += check_scope(said, facts=facts, labels=labels, period_name=period_name)
    held = {_plain(str(one)) for one in supplied}
    if held:
        for line in said:
            for found in FIGURE.findall(DATE.sub(" ", line)):
                if _plain(found) in held:
                    continue
                out.append(f"NUMBER: {found} appears in the publication and was not supplied, "
                           f"in: \"{line[:90]}\"")
    return out
