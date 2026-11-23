"""The governed adapter contract (implementation plan, Task 3).

`ForecastModelAdapter.forecast(context) -> ForecastOutput`

A context is everything a method may see: the exact series identity, the cutoff, the target history
as it stood at that cutoff, the horizons asked, and whatever supporting series were visible at the
cutoff. An output is the method's identity and version, its points (central value and quantiles per
horizon), an execution status and diagnostics.

The status is the part the tournament and the review depend on, so its meaning is fixed here:

- SUCCEEDED   - the method answered every horizon it was asked.
- FAILED      - the method raised, or answered something unusable. The error is in the output, the
                adapter did not try another method, and the caller decides what a failure means.
- INELIGIBLE  - the method cannot be asked this question: too little history, a conditional input
                absent at the cutoff, a market it does not cover. Not a failure - the method is
                simply not a candidate for this series/origin/horizon.
- UNAVAILABLE - the method's runtime is not installed here (a foundation model without torch, a
                LightGBM method without lightgbm). The method exists in the registry; this machine
                cannot run it. Reported so a review can say so rather than silently scoring without it.

One rule is enforced rather than documented: an adapter never raises. `forecast` catches whatever
the method throws and returns FAILED with the error named. Review Focus 3 of the CR (an override
method failing at forecast time must be visible, never silently replaced) rests on this.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Mapping, Sequence

from app.forecasting.adapter import (
    DEFAULT_HORIZONS, AsOfViolation, ForecastPoint, ForecastRequest, ForecastResult, Series,
    period_after, steps_for,
)


class ExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INELIGIBLE = "INELIGIBLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ForecastContext:
    """What a method is allowed to see, and nothing after the cutoff.

    `history` is the target series as it stood at `cutoff` (built by `app.forecasting.history`, which
    is where availability is enforced). `related` are the supporting series a conditional method may
    use, each already cut at the cutoff; `features` likewise for intelligence inputs. A context whose
    series carry a point after the cutoff refuses to exist - the same discipline `ForecastRequest`
    enforces, applied one layer up.
    """

    identity_key: str
    chemical: str
    cutoff: date
    history: Series
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    related: tuple[Series, ...] = ()
    features: tuple[Series, ...] = ()
    known_future: tuple[Series, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.horizons or any(h < 1 for h in self.horizons):
            raise ValueError("horizons must be one or more calendar weeks, each at least 1")
        for series in (self.history, *self.related, *self.features):
            late = [day for day in series.dates if day > self.cutoff]
            if late:
                raise AsOfViolation(
                    f"{series.key} carries {len(late)} point(s) after the cutoff "
                    f"{self.cutoff.isoformat()}, first {late[0].isoformat()}")

    def request(self, **overrides: Any) -> ForecastRequest:
        """The engine-protocol request this context amounts to."""
        fields: dict[str, Any] = dict(series=self.history, as_of=self.cutoff, horizons=self.horizons,
                                      covariates=self.related, known_future=self.known_future,
                                      config=dict(self.config))
        fields.update(overrides)
        return ForecastRequest(**fields)


@dataclass(frozen=True)
class ForecastOutput:
    method_key: str
    method_version: str
    status: ExecutionStatus
    cutoff: date
    points: tuple[ForecastPoint, ...] = ()
    reason: str = ""                       # why FAILED / INELIGIBLE / UNAVAILABLE, in words
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED

    def at(self, horizon_weeks: int) -> ForecastPoint | None:
        return next((p for p in self.points if p.horizon_weeks == horizon_weeks), None)

    def value(self, horizon_weeks: int) -> float | None:
        point = self.at(horizon_weeks)
        return None if point is None else float(point.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method_key, "version": self.method_version, "status": self.status.value,
            "cutoff": self.cutoff.isoformat(), "reason": self.reason,
            "points": [{"horizon_weeks": p.horizon_weeks, "period": p.period.isoformat(),
                        "value": round(p.value, 4), "quantiles": {str(q): round(v, 4) for q, v in p.quantiles.items()},
                        "steps": p.steps, "resolution": p.resolution, "exact": p.exact}
                       for p in self.points],
            "diagnostics": dict(self.diagnostics), "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class Ineligible(Exception):
    """Raised inside `_run` to say the method cannot be asked this question. Becomes INELIGIBLE."""


class Unavailable(Exception):
    """Raised inside `_run` when the runtime is not installed here. Becomes UNAVAILABLE."""


class ForecastModelAdapter:
    """Subclass, set `method_key` and `version`, implement `_run`. Never override `forecast`."""

    method_key: str = ""
    version: str = "1"
    kind: str = "UNIVERSAL"                 # UNIVERSAL | CONDITIONAL | CONTROL

    def forecast(self, context: ForecastContext) -> ForecastOutput:
        started = time.perf_counter()
        try:
            output = self._run(context)
        except Ineligible as why:
            return self._outcome(context, ExecutionStatus.INELIGIBLE, str(why), started)
        except Unavailable as why:
            return self._outcome(context, ExecutionStatus.UNAVAILABLE, str(why), started)
        except AsOfViolation as why:
            # A method that reached past the cutoff is a failure of the method, not of the data.
            return self._outcome(context, ExecutionStatus.FAILED, f"as-of violation: {why}", started)
        except Exception as why:             # noqa: BLE001 - the contract: a failure is reported, never raised
            return self._outcome(context, ExecutionStatus.FAILED, f"{type(why).__name__}: {why}", started,
                                 {"traceback": traceback.format_exc()[-2000:]})
        missing = [h for h in context.horizons if output.at(h) is None]
        if missing and output.status is ExecutionStatus.SUCCEEDED:
            # Part of an answer is not an answer: a method marked on three horizons and present on
            # two would be marked on a different exam. It stays INELIGIBLE for the horizons it did
            # not answer, and the tournament scores it only where it did.
            return ForecastOutput(output.method_key, output.method_version, ExecutionStatus.SUCCEEDED,
                                  output.cutoff, output.points,
                                  reason=f"answered {len(output.points)} of {len(context.horizons)} horizons",
                                  diagnostics={**output.diagnostics, "unanswered_horizons": missing},
                                  elapsed_seconds=time.perf_counter() - started)
        return ForecastOutput(output.method_key, output.method_version, output.status, output.cutoff,
                              output.points, output.reason, output.diagnostics,
                              elapsed_seconds=time.perf_counter() - started)

    # -- for subclasses -------------------------------------------------------------------------

    def _run(self, context: ForecastContext) -> ForecastOutput:
        raise NotImplementedError

    def succeeded(self, context: ForecastContext, points: tuple[ForecastPoint, ...] | list[ForecastPoint],
                  diagnostics: Mapping[str, Any] | None = None) -> ForecastOutput:
        if not points:
            raise Ineligible("the method returned no points")
        return ForecastOutput(self.method_key, self.version, ExecutionStatus.SUCCEEDED, context.cutoff,
                              tuple(points), diagnostics=dict(diagnostics or {}))

    def from_result(self, context: ForecastContext, result: ForecastResult,
                    diagnostics: Mapping[str, Any] | None = None) -> ForecastOutput:
        """An engine `ForecastResult` under this adapter's name. Empty points = the model declined."""
        if not result.points:
            raise Ineligible(str(result.metadata.get("reason") or "the model declined this series"))
        return self.succeeded(context, result.points, {**dict(result.metadata), **dict(diagnostics or {})})

    def _outcome(self, context: ForecastContext, status: ExecutionStatus, reason: str, started: float,
                 diagnostics: Mapping[str, Any] | None = None) -> ForecastOutput:
        return ForecastOutput(self.method_key, self.version, status, context.cutoff, (), reason,
                              dict(diagnostics or {}), elapsed_seconds=time.perf_counter() - started)


def points_from_path(context: ForecastContext, central: Sequence[float],
                     quantiles: Mapping[float, Sequence[float]] | None = None) -> tuple[ForecastPoint, ...]:
    """Turn a path of per-step values into one point per horizon asked.

    `central[k]` is the value k+1 periods after the last bucket, in the series' own frequency. Each
    calendar horizon is resolved to steps the way the engine's models do it (`steps_for`, with the
    series' offset), so a foundation model and the ETS control answer the same question on the same
    date. A horizon the path is too short for is left out, and the adapter reports it as unanswered.
    """
    series = context.history
    out = []
    for weeks in context.horizons:
        steps, exact = steps_for(weeks, series.frequency, series.offset_days)
        if steps < 1 or steps > len(central):
            continue
        ahead = steps_for(weeks, series.frequency)[0]
        out.append(ForecastPoint(
            horizon_weeks=weeks, period=period_after(series.last_date, steps, series.frequency),
            value=float(central[steps - 1]),
            quantiles={q: float(path[steps - 1]) for q, path in (quantiles or {}).items() if steps <= len(path)},
            steps=steps, resolution=series.frequency, exact=exact, steps_ahead=ahead))
    return tuple(out)


def require(module: str, *, for_method: str, hint: str = "") -> Any:
    """Import a runtime dependency or say, in words, that this machine does not have it."""
    import importlib

    try:
        return importlib.import_module(module)
    except Exception as why:                # ImportError, and torch's own load-time errors
        raise Unavailable(f"{for_method} needs {module!r}, which is not installed here"
                          + (f" ({hint})" if hint else "") + f": {type(why).__name__}: {why}")
