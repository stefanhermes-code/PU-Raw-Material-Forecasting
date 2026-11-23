"""Reading a finished edition back before anyone is offered it.

Stefan handed over the V2.1 specification on 11 October 2026 and this package was built to render
it as a designed PDF. On 12 October he settled what the output is: "I do NOT need a PDF, I will
review Word, approve (or small edits) and then save as PDF for the customer." One report, in the
file he edits, so a second document that could disagree with it has no reason to exist. The
renderer, its modules, its charts and its page style went to `_archive/superseded`, and everything
V2.1 asked the publication to carry - the cover, the dashboard, the forecast chapter, the chain
diagrams, the observed window under every chart and the back matter - is carried by the Word
report itself, in `period_report_docx`.

What stays here is the release control. V2.1 section 10 is not a courtesy: "Any discrepancy blocks
release." Every figure printed in the document that reaches a customer is traced back to the fact
pack, and every scope word is checked against the window it describes.
"""
from __future__ import annotations
