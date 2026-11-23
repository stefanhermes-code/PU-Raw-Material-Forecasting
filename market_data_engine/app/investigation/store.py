"""Opening, reading and advancing cases. Nothing here decides anything; it records.

A case is opened with its whole chain already written down as pending enquiries, so what has not
been asked yet is as visible as what has. That is the difference between an investigation and a
rejection with a note: the next question exists before anyone goes looking for it.

A case is never deleted and never closes as discarded. The three endings are RESOLVED, NARROWED and
EXPLAINED, and the last of those - why the answer does not exist - is a finding.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (InvestigationCandidateModel, InvestigationCaseModel,
                           InvestigationEnquiryModel)
from app.investigation import chain
from app.investigation import model as m


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def already_open(session: Session, *, observed: str, source_id: str | None,
                 observed_at=None) -> InvestigationCaseModel | None:
    """The same observation does not open a second case every time collection runs."""
    found = session.execute(
        select(InvestigationCaseModel)
        .where(InvestigationCaseModel.observed == observed)
        .where(InvestigationCaseModel.source_id == source_id)).scalars().all()
    when = _when(observed_at)
    for case in found:
        if when is None or case.observed_at is None:
            return case
        if case.observed_at.date() == when.date():
            return case
    return None


def open_case(session: Session, case: m.Case) -> InvestigationCaseModel:
    """Record the case and write its chain out as pending enquiries. Idempotent per observation."""
    if case.gap not in m.GAPS:
        raise ValueError(f"{case.gap!r} is not one of {m.GAPS}")
    existing = already_open(session, observed=case.observed, source_id=case.source_id or None,
                            observed_at=case.observed_at)
    if existing is not None:
        return existing
    row = InvestigationCaseModel(
        gap=case.gap, observed=case.observed, observed_at=_when(case.observed_at),
        source_id=case.source_id or None, evidence_id=case.evidence_id or None,
        origin_kind=case.origin_kind, origin_ref=case.origin_ref or None,
        chemical=case.chemical or None, geography=case.geography or None,
        state=m.OPEN, data=dict(case.data or {}))
    session.add(row)
    session.flush()
    for step_number, step in enumerate(chain.steps_for(case.gap), start=1):
        session.add(InvestigationEnquiryModel(
            case_id=row.id, question=step.question, step=step_number, asked=step.asked,
            capability=step.capability,
            # A step with no capability behind it is never "pending": nothing is coming to answer
            # it. It is a collection requirement from the moment the case opens.
            outcome=m.UNANSWERABLE if chain.unanswerable(step) else m.PENDING))
    session.flush()
    return row


def cases(session: Session, *, state: str | None = None, gap: str | None = None
          ) -> list[InvestigationCaseModel]:
    query = select(InvestigationCaseModel).order_by(InvestigationCaseModel.opened_at)
    if state:
        query = query.where(InvestigationCaseModel.state == state)
    if gap:
        query = query.where(InvestigationCaseModel.gap == gap)
    return list(session.execute(query).scalars().all())


def enquiries(session: Session, case_id: str, *, outcome: str | None = None
              ) -> list[InvestigationEnquiryModel]:
    query = (select(InvestigationEnquiryModel)
             .where(InvestigationEnquiryModel.case_id == case_id)
             .order_by(InvestigationEnquiryModel.step))
    if outcome:
        query = query.where(InvestigationEnquiryModel.outcome == outcome)
    return list(session.execute(query).scalars().all())


def next_enquiry(session: Session, case_id: str) -> InvestigationEnquiryModel | None:
    """The cheapest question not yet asked. None when the chain has been worked through."""
    pending = enquiries(session, case_id, outcome=m.PENDING)
    return pending[0] if pending else None


def record(session: Session, enquiry_id: str, *, outcome: str, found: str = "",
           data: dict | None = None) -> InvestigationEnquiryModel:
    """What an enquiry found. An enquiry that found nothing says so; it is never left blank."""
    if outcome not in m.OUTCOMES:
        raise ValueError(f"{outcome!r} is not one of {m.OUTCOMES}")
    row = session.get(InvestigationEnquiryModel, enquiry_id)
    if row is None:
        raise KeyError(enquiry_id)
    row.outcome = outcome
    row.found = found or None
    row.data = dict(data or {})
    row.ran_at = _now()
    case = session.get(InvestigationCaseModel, row.case_id)
    if case is not None:
        case.updated_at = _now()
    session.flush()
    return row


def add_candidates(session: Session, case_id: str, candidates) -> int:
    """Suspects from our own map. Re-running an enquiry does not duplicate them."""
    held = {(c.name, c.geography) for c in session.execute(
        select(InvestigationCandidateModel)
        .where(InvestigationCandidateModel.case_id == case_id)).scalars().all()}
    added = 0
    for one in candidates:
        key = (one.get("name"), one.get("geography"))
        if key in held:
            continue
        held.add(key)
        session.add(InvestigationCandidateModel(case_id=case_id, **one))
        added += 1
    session.flush()
    return added


def candidates(session: Session, case_id: str) -> list[InvestigationCandidateModel]:
    return list(session.execute(
        select(InvestigationCandidateModel)
        .where(InvestigationCandidateModel.case_id == case_id)
        .order_by(InvestigationCandidateModel.name)).scalars().all())


def rule_out(session: Session, candidate_id: str, why: str) -> None:
    """A suspect ruled out says what ruled it out. Nothing is removed."""
    row = session.get(InvestigationCandidateModel, candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    row.state = "RULED_OUT"
    row.why = why
    session.flush()


def close(session: Session, case_id: str, *, state: str, answer: str) -> InvestigationCaseModel:
    """End the case on an answer or on a reason. There is no ending that says nothing."""
    if state not in (m.RESOLVED, m.NARROWED, m.EXPLAINED):
        raise ValueError(f"a case closes RESOLVED, NARROWED or EXPLAINED, not {state!r}")
    if not str(answer or "").strip():
        raise ValueError("a case cannot be closed without saying what the answer or the reason is")
    row = session.get(InvestigationCaseModel, case_id)
    if row is None:
        raise KeyError(case_id)
    row.state = state
    row.answer = answer
    row.updated_at = _now()
    session.flush()
    return row
