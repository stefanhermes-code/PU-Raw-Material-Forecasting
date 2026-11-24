"""From a ranking to a PROPOSED automatic selection, per series and horizon (plan, Task 6).

Proposed, not active. Workpackage 2 ends with a deterministic winner for each series-horizon pair
and changes nothing the live forecast reads; activation is Workpackage 3's (first selection) and
Workpackage 4's (the quarterly review, committed atomically or not at all).

The winner is the best-ranked method that is PRODUCTION_ELIGIBLE in the governance registry. A
Challenger that out-scored it is reported beside it - that is the evidence an administrator
promotes on - and a flat control is never a winner, however it scored (CR: unchanged-price
rejection). Whether the winner beat the flat control is reported too: a proposal that does not beat
"no change" is still the best eligible method, but the review should be able to see that it is not
yet worth much.

A LEAD HAS TO BE SHOWN (v1.2.119). The standard method (`STANDARD_METHOD_KEY`) keeps the place
while the best-scoring method's lead over it is inside the noise of the test - see
`scoring.within_the_noise`. Ranking alone gave a market's forecast to whichever method was ahead at
the second decimal, and on 23 November 2026 that was 0.36 yuan on 26 dates.

The registry is read through a plain mapping of method key to status so this module needs no
session: the review passes what it loaded, a test passes a dict.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.domain.enums import ForecastMethodStatus, SelectionSource
from app.forecasting.domain import FLAT_CONTROL_KEYS, STANDARD_METHOD_KEY, ForecastMethodSelection
from app.forecasting.scoring import MethodScore, within_the_noise
from app.forecasting.tournament import TournamentResult


@dataclass(frozen=True)
class ProposedSelection:
    identity_key: str
    horizon_weeks: int
    method_key: str | None                 # None: nothing eligible was ranked at this horizon
    method_version: str = ""
    mae: float | None = None
    directional_accuracy: float | None = None
    matched_dates: int = 0
    rank: int | None = None                # the winner's place in the FULL ranking, challengers included
    beats_flat_control: bool | None = None
    challenger_ahead: str = ""             # the best-ranked Challenger that out-scored the winner, if any
    kept_ahead_of: str = ""                # the eligible method that scored better, by less than chance gives
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def to_selection(self, identity_id: str, method_id: str, *, review_id: str | None = None,
                     actor: str = "SYSTEM", selected_at: datetime | None = None) -> ForecastMethodSelection:
        """The record a later workpackage would activate. Built here so the shape is decided once."""
        if self.method_key is None:
            raise ValueError("no method was proposed for this series/horizon")
        return ForecastMethodSelection(
            identity_id=identity_id, horizon_weeks=self.horizon_weeks, method_id=method_id,
            method_version=self.method_version, source=SelectionSource.AUTOMATIC,
            selected_at=selected_at or datetime.now(timezone.utc), review_id=review_id, actor=actor,
            reason=self.reason or f"ranked first among production-eligible methods on {self.matched_dates} matched dates")


def propose(result: TournamentResult, status_of: Mapping[str, ForecastMethodStatus]) -> list[ProposedSelection]:
    """One proposal per horizon of the tournament result."""
    proposals = []
    for weeks in result.horizons:
        ranked = result.ranked(weeks)
        proposals.append(propose_for(result.identity_key, weeks, ranked, status_of))
    return proposals


def propose_for(identity_key: str, horizon_weeks: int, ranked: list[MethodScore],
                status_of: Mapping[str, ForecastMethodStatus]) -> ProposedSelection:
    eligible = [m for m in ranked if status_of.get(m.method_key) is ForecastMethodStatus.PRODUCTION_ELIGIBLE
                and m.method_key not in FLAT_CONTROL_KEYS]
    if not eligible:
        return ProposedSelection(identity_key, horizon_weeks, None,
                                 reason="no production-eligible method was ranked at this horizon")
    best = eligible[0]
    winner, held = best, None
    standard = next((m for m in eligible if m.method_key == STANDARD_METHOD_KEY), None)
    if standard is not None and standard is not best:
        held = within_the_noise(best, standard)
        if held is not None:
            winner = standard
    control = next((m for m in ranked if m.method_key in FLAT_CONTROL_KEYS), None)
    beats = None
    if control is not None and control.mae is not None and winner.mae is not None:
        beats = winner.mae < control.mae
    ahead = next((m.method_key for m in ranked
                  if m.rank is not None and winner.rank is not None and m.rank < winner.rank
                  and m.method_key not in FLAT_CONTROL_KEYS
                  and status_of.get(m.method_key) is ForecastMethodStatus.CHALLENGER), "")
    reason = (f"ranked {winner.rank} overall and first among production-eligible methods on "
              f"{winner.matched_dates} matched dates (MAE {winner.mae})")
    if held is not None:
        reason = (f"the standard method, ranked {winner.rank} overall on {winner.matched_dates} matched dates "
                  f"(MAE {winner.mae}); {best.method_key} scored better (MAE {best.mae}) by {held[0]:.2f} a date, "
                  f"where chance alone gives {held[1]:.2f} - a lead that has not been shown")
    if ahead:
        reason += f"; challenger {ahead} scored better and is reported for review"
    if beats is False:
        reason += "; does not beat the no-change control"
    return ProposedSelection(identity_key, horizon_weeks, winner.method_key, winner.method_version, winner.mae,
                             winner.directional_accuracy, winner.matched_dates, winner.rank, beats, ahead,
                             best.method_key if held is not None else "", reason)
