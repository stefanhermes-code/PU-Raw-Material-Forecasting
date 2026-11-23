"""The quarterly Model Fit Review as a governed, recorded act (Adaptive Forecast Method Selection, WP4, Task 9).

`ModelFitReviewService.run(cutoff, actor)`:

1. **Opens** a review (RUNNING) and commits that, so a crash leaves a trace.
2. **Computes everything first** - every eligible series, every horizon, every governed method - with the
   WP2 tournament (`review_job.run_review`). Nothing is written while this runs.
3. **Commits in one transaction**: every method's result per series and horizon (permanent
   `model_fit_result` rows, with the frequency examined), and - only when the store setting
   `automatic_selection_enabled` is on (decision D7) - the new automatic selection per pair, resting on
   this review. Manual overrides are never touched; where an override's method differs from the review's
   winner the review records an OVERRIDE RECOMMENDATION, never applied.
4. **Any failure** - the computation raising for any series, or the commit failing - rolls everything
   back, closes the review FAILED with the reason, and leaves the last successful selections in force
   (Review Focus 4). A method failing at some origins is a scored result, not a review failure.

A pair whose series is not eligible this time, or whose horizon has no ranked Production Eligible
method, keeps its previous selection; the review notes it.
"""
from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import select

from app.db.models import MarketIdentityModel, ModelFitReviewModel
from app.domain.enums import ReviewStatus, SelectionSource
from app.forecasting.adapters.base import ForecastModelAdapter
from app.forecasting.domain import ForecastMethodSelection, ModelFitResult, ModelFitReview
from app.forecasting.governance import (
    ForecastMethodRegistry, ForecastOverrideRepository, ForecastSelectionRepository, ModelFitReviewRepository,
)
from app.forecasting.review_job import identity_key_of, run_review
from app.forecasting.settings import get_setting

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Written:
    """What has been stored so far, across the per-market commits."""
    markets: int = 0
    written: int = 0
    changes: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    # Which markets are already in the store, so the close can write whatever is left and write
    # nothing twice.
    done: set = field(default_factory=set)


class ModelFitReviewService:
    def __init__(self, session_factory, *, compute: Callable[..., dict[str, Any]] = run_review,
                 registry=None) -> None:
        self.session_factory = session_factory
        self.compute = compute
        self.registry = registry

    def run(self, cutoff: date, actor: str = "SYSTEM", *, identity_keys: Iterable[str] | None = None,
            horizons: tuple[int, ...] = (4, 8, 12), max_origins: int | None = None,
            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        # **A review of one market commits that market's choice.** It used to be classed a
        # "partial look" and forbidden from committing anything - only a pass over every market
        # could change a method - and that single rule is what made continuous monitoring
        # impossible rather than merely unscheduled. Re-checking TDI China when sixty new readings
        # landed would have scored it and then refused to act on the answer.
        #
        # Stefan, 29 October 2026: "I want a continuously monitoring mechanism in place, inside the
        # application. No outside batch files, not historic reviews, just get rid of these. Today we
        # do a run, then that is the true North and after the monitoring mechanism takes over."
        #
        # What remains true: a review is RECORDED whatever its scope, and every selection says which
        # review chose it and why. A narrower look is still narrower - it is marked `scope` so the
        # record says what was examined - but narrow is not a reason to throw the answer away.
        #
        # `review_origins` is still the default depth per market.
        with self.session_factory() as session:
            standard = int(get_setting(session, "review_origins"))
        if max_origins is None:
            max_origins = standard
        scope = ("one market" if identity_keys is not None and len(list(identity_keys)) == 1
                 else "some markets" if identity_keys is not None
                 else "every market")
        identity_keys = list(identity_keys) if identity_keys is not None else None
        with self.session_factory() as session:
            close_abandoned(session)
            busy = running_review(session)
            if busy is not None:                       # WP4 gate condition 3: one review at a time
                session.commit()
                return {"review_id": None, "status": "REFUSED",
                        "reason": f"review {busy.id} has been running since {busy.started_at.isoformat()}"}
            review = ModelFitReviewRepository(session).open(ModelFitReview(
                cutoff=datetime.combine(cutoff, datetime.min.time(), timezone.utc), started_at=_now(), actor=actor,
                notes=json.dumps({"scope": scope, "max_origins": max_origins,
                                  # Which markets, by name. The watch reads this to avoid picking a
                                  # market whose last attempt failed minutes ago and failing again
                                  # on the next tick, for ever.
                                  "markets": list(identity_keys or ())})))
            session.commit()
        beat = _Heartbeat(self.session_factory, review.id, progress)
        with self.session_factory() as session:
            enabled = bool(get_setting(session, "automatic_selection_enabled"))
        bag = _Written()
        try:
            asked = dict(cutoff=cutoff, identity_keys=identity_keys, horizons=horizons,
                         max_origins=max_origins, registry=self.registry, progress=beat)
            # A fake compute in a test need not take the hook; the real one does.
            if "on_series" in inspect.signature(self.compute).parameters:
                asked["on_series"] = lambda entry: self._write_series(review.id, [entry], enabled, bag)
            report = self.compute(self.session_factory, **asked)
            if not report.get("series"):
                # Finding 2 (third review): a review that ranked nothing has examined nothing and must
                # not reset the quarterly clock.
                raise RuntimeError(f"no market was ranked ({len(report.get('eligibility', []))} judged)")
            summary = self._finish(review.id, report, bag, enabled=enabled, scope=scope,
                                   asked_for=identity_keys)
        except Exception as exc:
            # The markets already written STAY written (v1.2.25). `_close_failed` only marks the
            # review row, and the fits under it are the expensive thing. What a failed review must
            # not do is reset the quarterly clock, and it does not: `_latest_full_success` only
            # reads SUCCEEDED rows.
            log.exception("Model Fit Review %s failed after %s markets", review.id, bag.markets)
            reason = f"{type(exc).__name__}: {exc}"[:2000]
            _close_failed(self.session_factory, review.id, reason, scope,
                          markets=list(identity_keys or ()))
            return {"review_id": review.id, "status": ReviewStatus.FAILED.value, "reason": reason,
                    "markets_written": bag.markets, "results_written": bag.written}
        return {"review_id": review.id, "status": ReviewStatus.SUCCEEDED.value, **summary}

    def _write_series(self, review_id: str, entries: list[dict[str, Any]], enabled: bool,
                      bag: "_Written") -> None:
        """These markets' fit results and chosen methods, stored NOW in their own transaction.

        Called once per market as the review finishes it (v1.2.25). Stefan, 29 October 2026: "when
        found then write it, no need to wait till all is finished." A review of eighty markets takes
        most of a day, and holding everything to the end meant a crash at market seventy threw away
        all seventy fits.

        One market's write is one transaction, so a failure costs that market and nothing before it.
        What a half-finished review must still not do is reset the quarterly clock, and it does not:
        `_latest_full_success` reads SUCCEEDED rows only, and this never closes the review.
        """
        with self.session_factory() as session:
            try:
                methods = {m.method_key: m for m in ForecastMethodRegistry(session).list_all()}
                identities = {identity_key_of(row): row.id for row in session.scalars(select(MarketIdentityModel))}
                reviews = ModelFitReviewRepository(session)
                selections = ForecastSelectionRepository(session)
                overrides = ForecastOverrideRepository(session)
                written = 0
                changes: list[dict[str, Any]] = []
                kept: list[dict[str, Any]] = []
                recommendations: list[dict[str, Any]] = []
                for entry in entries:
                    key = entry["identity_key"]
                    identity_id = identities.get(key)
                    if identity_id is None:
                        raise RuntimeError(f"review names a market the store does not hold: {key}")
                    tournament = entry["tournament"]
                    frequency = tournament.get("frequency")
                    for weeks, rows in (tournament.get("ranking") or {}).items():
                        for row in rows:
                            method = methods.get(row["method"])
                            if method is None:
                                continue
                            counts = row.get("statuses") or {}
                            status = ("SUCCEEDED" if row.get("ranked") else
                                      max(counts, key=counts.get) if counts else "INELIGIBLE")
                            reviews.add_result(ModelFitResult(
                                review_id=review_id, identity_id=identity_id, horizon_weeks=int(weeks),
                                method_id=method.id, method_version=row.get("version") or method.version,
                                execution_status=status, matched_mae=row.get("mae"),
                                directional_accuracy=row.get("directional_accuracy"),
                                matched_dates=int(row.get("matched_dates") or 0), rank=row.get("rank"),
                                details={**row, "frequency": frequency, "duplicate_of": entry.get("duplicate_of", ""),
                                         # The status the method had WHEN reviewed, so later evidence reads
                                         # the review as it was (WP4 gate).
                                         "method_status": method.status.value}))
                            written += 1
                    for proposal in entry.get("proposals") or []:
                        weeks = int(proposal["horizon_weeks"])
                        winner = proposal.get("method_key")
                        current = selections.get_active(identity_id, weeks)
                        if not winner:
                            kept.append({"identity_key": key, "horizon_weeks": weeks,
                                         "reason": proposal.get("reason") or "no ranked Production Eligible method"})
                            continue
                        method = methods[winner]
                        override = overrides.get_active(identity_id, weeks)
                        if override is not None and override.method_id != method.id:
                            recommendations.append({"identity_key": key, "horizon_weeks": weeks,
                                                    "override_method_id": override.method_id,
                                                    "review_recommends": winner,
                                                    "reason": proposal.get("reason", "")})
                        change = {"identity_key": key, "horizon_weeks": weeks, "method_key": winner,
                                  "previous_method_id": current.method_id if current else None,
                                  "applied": enabled}
                        changes.append(change)
                        if enabled:
                            selections.set_active(ForecastMethodSelection(
                                identity_id=identity_id, horizon_weeks=weeks, method_id=method.id,
                                method_version=proposal.get("method_version") or method.version,
                                source=SelectionSource.AUTOMATIC, selected_at=_now(), review_id=review_id,
                                reason=proposal.get("reason", "")))
                session.commit()
            except Exception:
                session.rollback()
                raise
        bag.markets += len(entries)
        bag.done.update(str(entry.get("identity_key", "")) for entry in entries)
        bag.written += written
        bag.changes.extend(changes)
        bag.kept.extend(kept)
        bag.recommendations.extend(recommendations)

    def _finish(self, review_id: str, report: dict[str, Any], bag: "_Written", *,
                enabled: bool, scope: str = "every market",
                asked_for: list[str] | None = None) -> dict[str, Any]:
        """Write anything the hook did not, then close the review SUCCEEDED.

        **Anything the hook did not.** A caller may pass a `compute` that does not take `on_series`
        - every fake compute in the tests is `lambda *_a, **_k: report` - and then no market is
        written as it finishes. The first version of the per-market write simply lost those: the
        writing had moved into the hook and nothing was left to catch the case, so three tests found
        no selection in the store at all. Writing here what is not already written covers both
        callers and cannot double-write, because `bag.done` names what is in.

        The last part needs the whole picture whatever happens: "no proposal was made for this
        market" can only be known once every market has been through.
        """
        # One market per transaction here too, not one batch for all of them. Written as a batch,
        # a market the store does not hold rolled back the markets before it in the same call - so
        # "a failure keeps what it had finished" held through the hook and not through this path.
        for entry in [entry for entry in report.get("series", [])
                      if str(entry.get("identity_key", "")) not in bag.done]:
            self._write_series(review_id, [entry], enabled, bag)
        written, changes = bag.written, bag.changes
        kept, recommendations = bag.kept, bag.recommendations
        with self.session_factory() as session:
            try:
                identities = {identity_key_of(row): row.id for row in session.scalars(select(MarketIdentityModel))}
                reviews = ModelFitReviewRepository(session)
                selections = ForecastSelectionRepository(session)
                proposed_pairs = {(identities.get(c["identity_key"]), c["horizon_weeks"]) for c in changes}
                proposed_pairs |= {(identities.get(k["identity_key"]), k["horizon_weeks"]) for k in kept}
                by_id = {v: k for k, v in identities.items()}
                # What the review was ASKED to cover, which is not what it managed to rank. A full
                # review that could not rank a market must still say that market kept its method -
                # that is the useful part. Keyed on the ask, my first version keyed it on the ranked
                # series and so dropped exactly those markets.
                #
                # None means every market, so nothing is filtered. A named list means a narrow
                # look, and then the other hundred markets are not "kept", they are simply not this
                # review's business.
                in_scope = ({identities.get(key) for key in asked_for}
                            if asked_for is not None else None)
                for selection in selections.list_active():
                    if in_scope is not None and selection.identity_id not in in_scope:
                        continue
                    if (selection.identity_id, selection.horizon_weeks) not in proposed_pairs:
                        kept.append({"identity_key": by_id.get(selection.identity_id, selection.identity_id),
                                     "horizon_weeks": selection.horizon_weeks,
                                     "reason": "not ranked in this review (not eligible or not included); "
                                               "the previous selection stays in force"})
                notes = {"automatic_selection_enabled": enabled, "scope": scope, "results_written": written,
                         "selections_proposed": len(changes),
                         "selections_applied": sum(1 for c in changes if c["applied"]),
                         "kept_previous": kept, "override_recommendations": recommendations,
                         "changes": changes, "series_ranked": len(report.get("series", [])),
                         "series_judged": len(report.get("eligibility", []))}
                reviews.close(review_id, ReviewStatus.SUCCEEDED, notes=json.dumps(notes, default=str))
                session.commit()
            except Exception:
                session.rollback()
                raise
        return notes


# A RUNNING review is alive while its heartbeat is younger than this. The heartbeat is renewed as the
# review works through its markets (every progress line, at most once a minute), so a review of any
# length stays alive; only one that has stopped - killed, PC asleep, crashed - goes quiet and is closed
# as abandoned (WP4 gate, fourth review, finding 1: a fixed 12 hours from the START threw away any
# review that ran longer).
RUNNING_WINDOW_HOURS = 2
RETRY_AFTER_HOURS = 24
HEARTBEAT_SECONDS = 60


class _Heartbeat:
    """Passed to the computation as its progress callback: forwards each line and renews the review's
    heartbeat in its notes (a separate short transaction; never fails the review)."""

    def __init__(self, session_factory, review_id: str, forward: Callable[[str], None] | None) -> None:
        self.session_factory, self.review_id, self.forward = session_factory, review_id, forward
        self.last = 0.0

    def __call__(self, line: str) -> None:
        if self.forward is not None:
            self.forward(line)
        import time
        if time.monotonic() - self.last < HEARTBEAT_SECONDS:
            return
        self.last = time.monotonic()
        try:
            with self.session_factory() as session:
                row = session.get(ModelFitReviewModel, self.review_id)
                if row is not None and row.status == ReviewStatus.RUNNING.value:
                    row.notes = json.dumps({**_notes_of(row), "heartbeat": _now().isoformat()})
                    session.commit()
        except Exception:                                   # noqa: BLE001 - a missed beat is not a failure
            log.warning("review %s heartbeat not written", self.review_id, exc_info=True)


def _alive_since(row) -> datetime:
    beat = _notes_of(row).get("heartbeat")
    if beat:
        try:
            return _aware(datetime.fromisoformat(beat))
        except ValueError:
            pass
    return _aware(row.started_at)


def _aware(moment):
    return None if moment is None else (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def running_review(session, now: datetime | None = None):
    """A RUNNING review whose heartbeat (or start) is younger than RUNNING_WINDOW_HOURS, or None."""
    now = now or _now()
    for row in session.scalars(select(ModelFitReviewModel).where(
            ModelFitReviewModel.status == ReviewStatus.RUNNING.value).order_by(ModelFitReviewModel.started_at.desc())):
        if (now - _alive_since(row)).total_seconds() < RUNNING_WINDOW_HOURS * 3600:
            return row
    return None


def close_abandoned(session, now: datetime | None = None) -> int:
    """Close as FAILED every review left RUNNING for longer than the window (a killed process, a PC that
    slept, a close that itself failed) - WP4 gate condition 2. Returns how many."""
    now = now or _now()
    closed = 0
    for row in session.scalars(select(ModelFitReviewModel).where(
            ModelFitReviewModel.status == ReviewStatus.RUNNING.value)):
        if (now - _alive_since(row)).total_seconds() >= RUNNING_WINDOW_HOURS * 3600:
            row.status = ReviewStatus.FAILED.value
            row.finished_at = now
            row.notes = json.dumps({**_notes_of(row), "failure": f"abandoned: no heartbeat for "
                                                                 f"{RUNNING_WINDOW_HOURS} hours"})
            closed += 1
    if closed:
        session.flush()
    return closed


def _notes_of(row) -> dict:
    try:
        return json.loads(row.notes or "{}") or {}
    except (TypeError, ValueError):
        return {"text": row.notes}


def _close_failed(session_factory, review_id: str, reason: str, scope: str = "",
                  markets: list[str] | None = None, attempts: int = 3) -> None:
    """Close a review FAILED, retrying; if the store will not take it, `close_abandoned` will later."""
    for attempt in range(attempts):
        try:
            with session_factory() as session:
                ModelFitReviewRepository(session).close(review_id, ReviewStatus.FAILED,
                                                        notes=json.dumps({"failure": reason, "scope": scope,
                                                                          "markets": list(markets or ())}))
                session.commit()
            return
        except Exception:                                   # noqa: BLE001 - logged; abandoned-close is the backstop
            log.exception("could not close review %s as FAILED (attempt %s)", review_id, attempt + 1)



# ----------------------------------------------------------------------------- the schedule (Task 10)


def last_successful_review(session):
    """The most recent review that finished. Was `_latest_full_success`, which skipped the ones
    marked partial because only a full pass counted towards the quarterly cadence. There is no
    cadence now and a one-market re-check is a real answer, so there is nothing to skip."""
    for row in session.scalars(select(ModelFitReviewModel).where(
            ModelFitReviewModel.status == ReviewStatus.SUCCEEDED.value).order_by(
            ModelFitReviewModel.finished_at.desc())):
        return row
    return None


def review_state(session_factory, now: datetime | None = None) -> dict[str, Any]:
    """What the application can say about method selection, with no calendar in it.

    `review_schedule` used to live here and answered "when is the next quarterly review due". It
    reported `cadence_months` 3 and `due_at` 20 January 2027 while exactly one review had ever run,
    because the only thing that could start one was a batch file somebody had to double-click.

    Stefan, 29 October 2026: "No outside batch files, not historic reviews, just get rid of these."
    So there is no cadence, no due date and no overdue count. A method is re-checked when a reading
    arrives that the choice has not seen - `raw_material_forecasting.method_watch` decides that, and
    the application runs it. What is left to report is whether a review is in flight, and when each
    market was last answered.
    """
    now = _aware(now) if now is not None else _now()
    with session_factory() as session:
        close_abandoned(session, now)
        session.commit()
        busy = running_review(session, now)
        enabled = bool(get_setting(session, "automatic_selection_enabled"))
        last = last_successful_review(session)
        failed = None
        for row in session.scalars(select(ModelFitReviewModel).where(
                ModelFitReviewModel.status == ReviewStatus.FAILED.value).order_by(
                ModelFitReviewModel.finished_at.desc())):
            failed = row
            break
    return {
        "state": "RUNNING" if busy is not None else ("ANSWERED" if last is not None else "NEVER_RUN"),
        "automatic_selection_enabled": enabled,
        "running_since": busy.started_at.isoformat() if busy is not None else None,
        "last_review_id": str(last.id) if last is not None else None,
        "last_at": last.finished_at.isoformat() if last is not None and last.finished_at else None,
        "last_scope": _notes_of(last).get("scope", "") if last is not None else "",
        "last_failed_at": failed.finished_at.isoformat()
                          if failed is not None and failed.finished_at else None,
    }
