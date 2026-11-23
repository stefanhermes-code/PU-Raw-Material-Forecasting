"""The application re-checks a market's forecast method when a reading arrives it has not seen.

Stefan, 29 October 2026: **"I want a continuously monitoring mechanism in place, inside the
application. No outside batch files, not historic reviews, just get rid of these. Today we do a
run, then that is the true North and after the monitoring mechanism takes over."**

WHAT THIS REPLACES
------------------
`review_cadence_months` = 3, `due_at` = 20 January 2027, one review ever run, and the only thing
that could start one was `Run-Model-Fit-Review.bat`. The project shipped Windows scheduled-task
definitions for the daily collection and for the test suite and never one for the review. All of
that is gone: the cadence, the due date, the `--if-due` check and both batch files.

HOW IT DECIDES
--------------
`raw_material_forecasting.method_watch.behind` holds the rule, and it is deliberately a pure
function over rows so it can be tested without a store and so the page and this worker cannot
disagree: a market is behind when it holds a reading that became READABLE after the last time a
review looked at it. `available_from`, not `observation_date` - a July customs figure arriving in
October is new evidence in October.

Two things this module has to supply, and the first version supplied neither:

**When each market was last LOOKED at**, not only when a method was chosen for it. A review that
looks at a market and finds nothing worth choosing has still looked, and keying the rule on
`selected_at` alone left such a market behind for ever. `_examined` answers it from the store: the
finish of the newest successful review that produced a result for that market.

**Which markets the review can rank at all.** Straight after the baseline run the first version put
245 markets in the queue; 242 of them have no method because the application cannot forecast them,
and the watch would have re-scored the same unforecastable market every fifteen minutes for ever.
`forecastable` asks `app.forecasting.eligibility` - the application's own rule, never a second copy
of it here.

WHY THE VERDICTS ARE REMEMBERED
-------------------------------
Measured against the live store on 29 October 2026: reading the price history of all 322 series
takes **138 seconds**, and judging them all then takes **0.2**. The read is the cost, not the rule.
So a market is re-judged only when it holds a reading newer than the one its verdict was made on,
which is a handful of markets a day. The first tick pays the 138 seconds once.

HOW IT ACTS
-----------
One market at a time, through the same `ModelFitReviewService` the baseline run used, so a
monitored re-check and the baseline are scored by one path. Until today a one-market review was
classed a "partial look" and forbidden from committing anything; that rule is gone, which is what
makes this possible at all.

A re-check that finds the method already in force still best changes nothing, and says so. Every
selection records which review chose it and why, so the history of a market's method is readable.

WHY ONE AT A TIME, SLOWLY
-------------------------
A market costs from thirty seconds to several minutes to score. Draining a queue of eighty at once
would take the machine for the day, which is what the baseline run is for. The watch is meant to
keep up with arrivals - a handful of markets a day - so it takes the market furthest behind, scores
it, and waits. `close_abandoned` and the one-review-at-a-time rule still apply, so a watch tick
during a baseline run does nothing and waits for the next tick.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from app.db.models import (
    ForecastMethodSelectionModel, MarketIdentityModel, PriceObservationModel,
)
from app.forecasting.review import ModelFitReviewService, running_review
from app.forecasting.review_job import identity_key_of

log = logging.getLogger(__name__)

# How long the watch waits between markets. Long enough that it never competes with collection or
# with a page render, short enough that a day's arrivals are caught up within the day.
SECONDS_BETWEEN_MARKETS = 15 * 60

# The actor recorded against a selection the watch changed, so the record says a machine did it
# because evidence arrived, not that a person decided.
ACTOR = "the watch (new readings arrived)"


def _rows(session) -> tuple[list, list, dict[str, str]]:
    """Selections, readings and market names, as the pure rule wants them."""
    selections = list(session.scalars(select(ForecastMethodSelectionModel)))
    readings = list(session.execute(
        select(PriceObservationModel.identity_id, PriceObservationModel.available_from)).all())
    names = {row.id: identity_key_of(row)
             for row in session.scalars(select(MarketIdentityModel))}
    return selections, readings, names


def recently_failed(session, now: datetime | None = None) -> set[str]:
    """Markets whose last re-check failed inside `RETRY_AFTER_HOURS`, so the watch leaves them be.

    Without this the watch picks the market furthest behind, fails on it, and picks the same one
    fifteen minutes later, for ever. The old quarterly schedule had the same protection -
    `RETRY_AFTER_HOURS`, state RETRY_LATER - and a test of it is what caught the omission here.

    A market comes back into the queue when the hours have passed. Nothing is given up on; it is
    only not hammered.
    """
    from app.db.models import ModelFitReviewModel
    from app.domain.enums import ReviewStatus
    from app.forecasting.review import RETRY_AFTER_HOURS, _aware, _notes_of

    now = _aware(now) if now is not None else datetime.now(timezone.utc)
    sore: set[str] = set()
    for row in session.scalars(select(ModelFitReviewModel).where(
            ModelFitReviewModel.status == ReviewStatus.FAILED.value)):
        when = row.finished_at or row.started_at
        if when is None or (now - _aware(when)).total_seconds() > RETRY_AFTER_HOURS * 3600:
            continue
        sore.update(_notes_of(row).get("markets") or ())
    return sore


def _examined(session) -> dict[str, datetime]:
    """When a review last LOOKED at each market: the finish of the newest successful review that
    produced a result for it.

    A result row is written for every method on every market the review ranked, whatever the
    outcome, so this covers exactly the markets that were examined - including the ones the evidence
    gave no answer for, which is the whole point of asking.
    """
    from sqlalchemy import func

    from app.db.models import ModelFitResultModel, ModelFitReviewModel
    from app.domain.enums import ReviewStatus

    rows = session.execute(
        select(ModelFitResultModel.identity_id, func.max(ModelFitReviewModel.finished_at))
        .join(ModelFitReviewModel, ModelFitReviewModel.id == ModelFitResultModel.review_id)
        .where(ModelFitReviewModel.status == ReviewStatus.SUCCEEDED.value)
        .group_by(ModelFitResultModel.identity_id)).all()
    return {str(row[0]): row[1] for row in rows if row[1] is not None}


# What the watch has judged, and the newest reading each verdict was made on. Held for the life of
# the process; a restart re-judges once. See WHY THE VERDICTS ARE REMEMBERED above.
_judged: dict[str, tuple[Any, bool]] = {}


class _OneSession:
    """Hands `forecast_rows` the same session every time instead of a connection every time.

    `forecast_rows` takes a session FACTORY and opens a session per call, which is right for a
    single read and wrong three hundred times in a row. The engine's pool is SQLAlchemy's default -
    five connections with ten overflow - and three background threads share it: the forecast warm-up,
    the boot housekeeping and this. The first tick after the restart of 30 October 2026 died on
    `QueuePool limit of size 5 overflow 10 reached, connection timed out`, and it died silently
    until `/imports` started carrying the tick (v1.2.31), which is what that change was for.

    The pass is read-only and strictly sequential, so one session is both correct and the obvious
    thing. `__exit__` deliberately does not close: the caller owns the session.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc) -> bool:
        return False


def forecastable(session_factory, newest: dict[str, Any], *,
                 memory: dict | None = None, as_of: date | None = None) -> set[str]:
    """The markets the eligibility rule admits, re-judged only where the readings have moved."""
    from app.forecasting.eligibility import Candidate, judge
    from app.forecasting.store_series import forecast_rows

    mem = _judged if memory is None else memory
    when = as_of or datetime.now(timezone.utc).date()
    able: set[str] = set()
    with session_factory() as session:
        identities = list(session.scalars(select(MarketIdentityModel)))
        one = _OneSession(session)
        for identity in identities:
            identity_id = str(identity.id)
            mark = newest.get(identity_id)
            held = mem.get(identity_id)
            if held is None or held[0] != mark:
                rows = forecast_rows(one, identity_id, all_versions=True)
                verdict = judge(Candidate(identity_key_of(identity),
                                          str(identity.product_family or "UNKNOWN"), rows), when)
                held = (mark, bool(verdict.eligible))
                mem[identity_id] = held
            if held[1]:
                able.add(identity_id)
    return able


def behind_now(session_factory, *, memory: dict | None = None) -> list:
    """Every market whose method has not seen its newest readings, furthest behind first."""
    from raw_material_forecasting.method_watch import behind

    with session_factory() as session:
        selections, readings, names = _rows(session)
        looked = _examined(session)

    newest: dict[str, Any] = {}
    for row in readings:
        identity_id = str(getattr(row, "identity_id", "") or "")
        arrived = getattr(row, "available_from", None)
        if identity_id and arrived is not None and (
                identity_id not in newest or arrived > newest[identity_id]):
            newest[identity_id] = arrived

    return behind(selections, readings, markets=names, examined=looked,
                  forecastable=forecastable(session_factory, newest, memory=memory))


def recheck_next(session_factory, *, registry=None,
                 progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Score the one market furthest behind and record the answer. Returns what it did.

    Does nothing while a review is already running: the store allows one at a time, and a baseline
    run must not be interrupted by the watch.
    """
    say = progress or (lambda _line: None)
    with session_factory() as session:
        busy = running_review(session)
        if busy is not None:
            return {"state": "a review is already running", "review_id": str(busy.id)}
        sore = recently_failed(session)

    queue = [row for row in behind_now(session_factory) if row.identity_key not in sore]
    if not queue:
        return {"state": "every method choice is current", "markets_behind": 0,
                "waiting_after_a_failure": len(sore)}

    worst = queue[0]
    say(f"the watch is re-checking {worst.market}: {worst.said()}")
    service = ModelFitReviewService(session_factory, registry=registry)
    answer = service.run(datetime.now(timezone.utc).date(), actor=ACTOR,
                         identity_keys=[worst.identity_key], progress=progress)
    return {"state": "re-checked", "market": worst.market, "markets_behind": len(queue),
            "readings_it_had_not_seen": worst.readings_since, **answer}


# The one watch this process runs. Held here rather than on the FastAPI app because the startup
# housekeeping that calls `start` has no `app` in scope - which is how the first version of this
# raised NameError and was caught by the "never allowed to break startup" guard around it.
_running: threading.Thread | None = None


def is_watching() -> bool:
    return _running is not None and _running.is_alive()


def stop_watching() -> None:
    """For a test, or an orderly shutdown. A daemon thread needs no stopping to exit."""
    global _running
    if _running is not None:
        getattr(_running, "stop", threading.Event()).set()
        _running = None


# What the last tick did, so the state of the watch can be read without a log file. See `start`.
_last: dict[str, Any] = {"state": "not started yet"}


def last_tick() -> dict[str, Any]:
    """What the watch did most recently. The engine serves this at `/imports`."""
    return dict(_last)


# How often the watch asks whether the engine has finished starting, before its first tick.
SECONDS_BETWEEN_READY_CHECKS = 5


def start(session_factory, *, registry=None,
          seconds_between: float = SECONDS_BETWEEN_MARKETS,
          report: Callable[[dict[str, Any]], None] | None = None,
          ready: Callable[[], bool] | None = None) -> threading.Thread:
    """Run the watch for as long as the application runs. Never raises into the caller.

    A daemon thread, so it dies with the application and never holds a shutdown open. One market per
    tick; a tick that finds nothing behind costs one query.

    Starting twice returns the watch already running: the engine's startup can run more than once in
    one process (a reload, a test fixture), and two watches would race for the same market.

    `report` is handed what each tick did. The watch writes to `log.info`, and uvicorn is started
    without a log config, so the root logger sits at WARNING and every one of those lines is
    dropped - the engine said "watching" and then nothing was readable anywhere. A mechanism that
    cannot be seen working is not much better than the batch file it replaced.

    `ready` holds the first tick back until the engine has finished its own startup. The first
    version of this ran the tick at the moment of boot and died on the connection pool; the second
    waited a whole interval, which is fifteen minutes of nothing for no stated reason. The condition
    is what matters, not a duration, so the engine is simply asked - and the wait is capped at one
    interval, so a `ready` that never turns true delays the watch instead of silencing it.
    """
    global _running
    if is_watching():
        return _running                          # type: ignore[return-value]
    stop = threading.Event()

    def loop() -> None:
        # The first tick waits until the engine has finished starting, and no longer. Boot is the
        # one minute the engine is busiest - the forecast warm-up preparing every market, the
        # housekeeping rebuilding consolidated series - and the first version ticked straight into
        # it and died on the pool. Nothing here is urgent enough to compete with that; nothing here
        # is slow enough to deserve a fifteen-minute pause either.
        if ready is not None:
            waited = 0.0
            while waited < seconds_between:
                try:
                    if ready():
                        break
                except Exception:        # a readiness check that throws must not stop the watch
                    log.warning("method watch could not ask whether the engine is ready",
                                exc_info=True)
                    break
                if stop.wait(SECONDS_BETWEEN_READY_CHECKS):
                    return
                waited += SECONDS_BETWEEN_READY_CHECKS
        while True:
            try:
                did = recheck_next(session_factory, registry=registry)
                log.info("method watch: %s", did.get("state"))
            except Exception as exc:             # never allowed to take the application down
                log.exception("method watch tick failed")
                did = {"state": "the tick failed", "error": str(exc)}
            _last.clear()
            _last.update({**did, "at": datetime.now(timezone.utc).isoformat()})
            if report is not None:
                try:
                    report(last_tick())
                except Exception:                # a report nobody reads must not stop the watch
                    log.warning("method watch could not report its tick", exc_info=True)
            if stop.wait(seconds_between):       # the only way out
                return

    thread = threading.Thread(target=loop, name="method-watch", daemon=True)
    thread.stop = stop                           # type: ignore[attr-defined]
    thread.start()
    _running = thread
    return thread
