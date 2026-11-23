from __future__ import annotations
from datetime import datetime, timezone
from ..domain import CandidateStateRecord
from ..repositories import MarketRepository


def _record(repo: MarketRepository, candidate_id: str, state: str, *, changed_by: str, reason: str, changed_at: datetime | None = None) -> CandidateStateRecord:
    r = CandidateStateRecord(candidate_id, state, changed_at or datetime.now(timezone.utc), changed_by, reason)
    repo.record_candidate_state(r)
    return r


def approve_candidate(repo: MarketRepository, candidate_id: str, *, changed_by: str, reason: str) -> CandidateStateRecord:
    return _record(repo, candidate_id, "APPROVED", changed_by=changed_by, reason=reason)


def reject_candidate(repo: MarketRepository, candidate_id: str, *, changed_by: str, reason: str) -> CandidateStateRecord:
    return _record(repo, candidate_id, "REJECTED", changed_by=changed_by, reason=reason)
