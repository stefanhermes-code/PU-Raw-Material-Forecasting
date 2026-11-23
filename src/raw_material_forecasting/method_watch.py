"""Keep each market's forecast method honest as new readings arrive.

Stefan, 29 October 2026: **"I want a continuously monitoring mechanism in place, inside the
application. No outside batch files, not historic reviews, just get rid of these. Today we do a
run, then that is the true North and after the monitoring mechanism takes over."**

WHAT WAS THERE INSTEAD
----------------------
A quarterly calendar and a button. Measured the same day: `review_cadence_months` = 3,
`due_at` = 20 January 2027, one review ever run (20 October 2026), and the only thing that could
start one was `Run-Model-Fit-Review.bat` - a file somebody has to double-click. The project ships
Windows scheduled-task definitions for the daily collection and for the test suite; there was never
one for the review. So the mechanism was neither continuous nor automatic, and its trigger was a
date rather than the arrival of evidence.

Worse, a review of ONE market was classified a "partial look" and forbidden from committing a
selection - only a full pass over everything could change a method. So re-checking TDI China when
sixty new readings landed would have scored it and then refused to act on the answer. That rule is
what made continuous monitoring impossible rather than merely unscheduled, and it goes with the
rest.

WHAT THIS IS
------------
A market's chosen method is **behind** when the market holds a reading that became readable after
the method was chosen. That is the whole definition, and it needs no new table: `selected_at` is
already on the selection and `available_from` is already on the reading.

`available_from` rather than `observation_date` on purpose. A customs month for July that arrives
in October is new evidence in October, and a method chosen in September has not seen it. The date a
reading REFERS to is not the date it became knowable, and every cutoff in this store is applied to
the latter.

A market with no selection at all is behind too - but only if it was never LOOKED AT. Measured
against the live store on 29 October 2026, straight after the baseline run, the first version of
this rule put **245 markets** in the queue, of which 242 had no method because the application
cannot forecast them at all: too little history, or no longer priced. The watch would have taken
the worst of them, spent a minute reading it, ranked nothing, written no selection, and picked the
same market again fifteen minutes later, for ever. Nothing in the loop would ever have moved.

So two things decide, not one:

- **The look, not the choice.** A market is behind when a reading arrived after the last time the
  market was EXAMINED. A review that looked and found nothing worth changing has still looked, and
  the market waits for the next reading like any other. Keying this on `selected_at` alone meant a
  market the evidence gave no answer for stayed behind for ever, which is the same loop reached from
  the other side.
- **Only markets the review can rank.** Eligibility is the application's own rule
  (`app.forecasting.eligibility`), and the caller applies it - this module never gets a second copy
  of it. A market that cannot be forecast is not behind; it is simply not forecastable, and the
  price card already says so.

The baseline run of 29 October 2026 is the "true North" Stefan names: from then on, every change to
a method is because a reading arrived and the evidence moved, and the record says which reading and
when.

WHAT IT DOES NOT DO
-------------------
It does not decide anything itself. It finds the markets whose choice has fallen behind, orders them
by how far behind they are, and hands them one at a time to the same scoring the baseline run used.
One market, one answer, one record - and if the evidence still prefers the method already in force,
nothing changes and that is recorded too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


@dataclass(frozen=True)
class Behind:
    """One market whose method choice has not seen everything the market holds."""

    identity_id: str
    identity_key: str
    market: str
    horizons: tuple[int, ...]
    chosen_at: datetime | None          # None where nothing has ever been chosen
    newest_reading: datetime
    readings_since: int
    examined_at: datetime | None = None    # when a review last looked at this market, chosen or not

    @property
    def never_judged(self) -> bool:
        return self.chosen_at is None and self.examined_at is None

    @property
    def last_seen(self) -> datetime | None:
        """The later of the choice and the look. This is what a reading has to beat."""
        seen = [when for when in (self.chosen_at, self.examined_at) if when is not None]
        return max(seen) if seen else None

    def said(self) -> str:
        readings = f"{self.readings_since:,} reading" + ("" if self.readings_since == 1 else "s")
        if self.never_judged:
            return f"{self.market}: no method has ever been chosen; {readings} held"
        if self.chosen_at is None:
            return (f"{self.market}: looked at {self.examined_at:%d %b %Y} and given no method; "
                    f"{readings} since")
        return f"{self.market}: chosen {self.chosen_at:%d %b %Y}, {readings} since"


# How far behind a market has to be before it is worth re-scoring. One reading moves a mean very
# little and a re-score costs half a minute to several minutes, so a market is left alone until it
# has something to say. Stefan has not set a number; this is the smallest one that is not "every
# single print", and it is in one place so it can be changed in one place.
READINGS_BEFORE_A_RECHECK = 1


def _aware(moment: datetime | None) -> datetime | None:
    """UTC-aware, whatever came in. Every stamp entering the rule goes through this.

    Not cosmetic: Python raises TypeError on comparing a naive datetime with an aware one, and the
    store hands back both kinds depending on the driver and the column. The rule does nothing but
    compare stamps, so one naive `selected_at` would have killed the tick with a TypeError rather
    than returning a wrong answer. Everything in this store is UTC; only the marker is missing.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def behind(selections: Sequence[Any], readings: Sequence[Any], *,
           markets: dict[str, str] | None = None,
           examined: dict[str, datetime] | None = None,
           forecastable: set[str] | None = None,
           at_least: int = READINGS_BEFORE_A_RECHECK) -> list[Behind]:
    """The markets whose method has not seen their newest readings, furthest behind first.

    `selections` carry `identity_id`, `horizon_weeks`, `selected_at` and `active`. `readings` carry
    `identity_id`, `available_from`. Both are plain rows, so this is testable without a store and
    the same function answers for the page and for the worker.

    `examined` says when a review last looked at each market, whether or not it produced a choice.
    `forecastable`, when given, is the set of markets the eligibility rule admits - anything outside
    it is left alone, because re-scoring a market the review cannot rank achieves nothing and the
    watch would do it again on the next tick. Both default to "unknown", which keeps the old
    behaviour for a caller that has neither.
    """
    names = markets or {}
    looked = {key: _aware(when) for key, when in (examined or {}).items()}
    newest: dict[str, datetime] = {}
    held: dict[str, list[datetime]] = {}
    for row in readings:
        identity_id = str(getattr(row, "identity_id", "") or "")
        when = _aware(getattr(row, "available_from", None))
        if not identity_id or when is None:
            continue
        held.setdefault(identity_id, []).append(when)
        if identity_id not in newest or when > newest[identity_id]:
            newest[identity_id] = when

    chosen: dict[str, datetime] = {}
    horizons: dict[str, set[int]] = {}
    for row in selections:
        if not getattr(row, "active", True):
            continue
        identity_id = str(getattr(row, "identity_id", "") or "")
        when = _aware(getattr(row, "selected_at", None))
        if not identity_id or when is None:
            continue
        horizons.setdefault(identity_id, set()).add(int(getattr(row, "horizon_weeks", 0) or 0))
        # The OLDEST choice on the market decides: if one horizon was picked before a reading
        # landed, that market needs looking at, whatever the other horizons say.
        if identity_id not in chosen or when < chosen[identity_id]:
            chosen[identity_id] = when

    out: list[Behind] = []
    for identity_id, newest_reading in newest.items():
        if forecastable is not None and identity_id not in forecastable:
            continue
        chosen_at = chosen.get(identity_id)
        examined_at = looked.get(identity_id)
        seen = [when for when in (chosen_at, examined_at) if when is not None]
        cutoff = max(seen) if seen else None
        since = [when for when in held[identity_id] if cutoff is None or when > cutoff]
        if len(since) < at_least:
            continue
        out.append(Behind(
            identity_id=identity_id,
            identity_key=names.get(identity_id, identity_id),
            market=names.get(identity_id, identity_id),
            horizons=tuple(sorted(horizons.get(identity_id, ()))),
            chosen_at=chosen_at,
            newest_reading=newest_reading,
            readings_since=len(since),
            examined_at=examined_at,
        ))

    # Never looked at first - a market nothing has ever judged is the worst case - then by how long
    # it has been blind, then by how much it has not seen.
    floor = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda row: (row.last_seen is not None, row.last_seen or floor,
                              -row.readings_since))
    return out



def next_to_recheck(selections: Sequence[Any], readings: Sequence[Any],
                    **rules: Any) -> Behind | None:
    """The single market most in need of a fresh answer, or None when every choice is current."""
    queue = behind(selections, readings, **rules)
    return queue[0] if queue else None
