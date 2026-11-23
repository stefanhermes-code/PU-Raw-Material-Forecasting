"""The production standard the report is written to, and the version of it in force.

Stefan, 10 October 2026: "we put the usage of the instructions properly in the application."

The text lives beside the data, in `data/config/report_standard.md`, so it can be read, edited and
handed to the PU Observatory without touching code. The version is held here as well, and a test
holds the two together, so a standard that moves on without the application noticing cannot happen
quietly.

What is enforced, rather than described, lives in `report_gates`. This module is what the standard
says; that module is what the document has to survive.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VERSION = "2.0"
ISSUED = "2026-10-10"

PROJECT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = PROJECT / "data" / "config" / "report_data_contract.md"
EDITORIAL_FILE = PROJECT / "data" / "config" / "report_editorial_standard.md"
# Charlie's production narrative instruction, 10 October 2026. It goes to the engine whole, as
# block one, before anything else. Stefan: "You are the engineer, Charlie is the salesperson."
# The engineering half stays in `report_data_contract.md`, where the gates read it, and is not
# sent: the instruction already carries everything the engine needs about figures and scope,
# and two documents saying the same thing in different words is how a rule drifts.
INSTRUCTION_FILE = PROJECT / "data" / "config" / "report_narrative_instruction.md"
# Version 1.1 of the single standard, kept so an edition issued under it can still be read.
STANDARD_FILE = PROJECT / "data" / "config" / "report_standard.md"


@dataclass(frozen=True)
class Standard:
    version: str
    issued: str
    contract: str = ""
    editorial: str = ""

    @property
    def held(self) -> bool:
        return bool(self.contract.strip() or self.editorial.strip())

    @property
    def text(self) -> str:
        return "\n\n".join(one for one in (self.contract, self.editorial) if one.strip())


def _read(where: Path) -> str:
    try:
        return where.read_text(encoding="utf-8")
    except OSError:
        return ""


def load(path: Path | None = None, editorial: Path | None = None) -> Standard:
    """The two halves in force."""
    contract = _read(Path(path) if path else CONTRACT_FILE)
    if not contract and not path:
        contract = _read(STANDARD_FILE)
    return Standard(version=VERSION, issued=ISSUED, contract=contract,
                    editorial=_read(Path(editorial) if editorial else EDITORIAL_FILE))


def narrative_instruction(path: Path | None = None) -> str:
    """Block one, exactly as the editor's writer wrote it.

    Nothing is added to it, trimmed from it or paraphrased. It is the instruction, and an
    engineer editing an editor's brief in transit is the problem this split exists to prevent.
    """
    return _read(Path(path) if path else INSTRUCTION_FILE)


def for_the_engine(path: Path | None = None, editorial: Path | None = None) -> str:
    """What is sent to the PU Observatory ahead of anything it is asked to write.

    Both halves go whole. Handing the engine a summary would leave it writing to a paraphrase,
    which is how a rule quietly loses a clause. They are labelled, because the two carry
    different authority: the contract is what the application can check, and the editorial
    standard is the editor's, to be followed as written and argued with through the editor.
    """
    standard = load(path, editorial)
    if not standard.held:
        return ""
    out = [f"Version {standard.version}, issued {standard.issued}."]
    if standard.contract.strip():
        out.append("THE DATA CONTRACT. What the application guarantees and what it checks. "
                   "These points are not open.\n\n" + standard.contract)
    if standard.editorial.strip():
        out.append("THE EDITORIAL STANDARD. How the report reads. This is the editor's and it "
                   "governs the writing.\n\n" + standard.editorial)
    return "\n\n".join(out)
