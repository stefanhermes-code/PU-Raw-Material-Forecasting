"""Chronos-2 and Chronos-Bolt behind the governed contract (research assets, Charlie 19 Oct 2026, 4.3).

Four universal configurations from the CR: Chronos-2 on the target history, Chronos-2 on the recent
36 months only, Chronos-2 on the log price, and Chronos-Bolt tiny. All are Challengers in the
registry; none runs on the page path.

The runtime is optional on purpose. `chronos-forecasting==2.3.2` and torch are not part of the
engine's requirements, and the interactive application never loads them; a review machine that has
them runs these methods, and one that does not gets UNAVAILABLE with the package named. The model
weights are fetched by the chronos package itself from the asset id (`amazon/chronos-2`,
`amazon/chronos-bolt-tiny`) the first time they are used and cached by it; a machine without network
access to the hub reports that as UNAVAILABLE too, rather than as a failure of the method.

Each adapter builds a plain list of floats, asks the pipeline for quantiles at the horizon in STEPS
of the series' own frequency, and maps the path back to the calendar horizons with the same
`steps_for` the engine's models use. A transformed input (log price) is inverted before the points
are built, so what the tournament scores is a price.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Sequence

from app.forecasting.adapter import DEFAULT_QUANTILES, MONTHLY, WEEKLY, DAILY, steps_for
from app.forecasting.adapters.base import (
    ForecastContext, ForecastModelAdapter, ForecastOutput, Ineligible, Unavailable, points_from_path, require,
)

CHRONOS2 = "amazon/chronos-2"
BOLT_TINY = "amazon/chronos-bolt-tiny"
PACKAGE = "chronos-forecasting==2.3.2"

# The fewest periods a foundation model is asked from. Below this the context window is mostly
# padding and the answer is the model's prior about "a series", not about this market.
MIN_CONTEXT = {DAILY: 60, WEEKLY: 16, MONTHLY: 8}

# Periods in "36 months" at each frequency, for the recent-window configuration.
PERIODS_PER_36_MONTHS = {DAILY: 36 * 21, WEEKLY: 156, MONTHLY: 36}


@lru_cache(maxsize=4)
def _pipeline(asset: str, kind: str):
    """One loaded pipeline per asset per process. Loading is the expensive part."""
    chronos = require("chronos", for_method=asset, hint=f"pip install {PACKAGE} torch")
    torch = require("torch", for_method=asset)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        if kind == "chronos2":
            pipeline_class = getattr(chronos, "Chronos2Pipeline", None) or getattr(chronos, "BaseChronosPipeline")
        else:
            pipeline_class = getattr(chronos, "BaseChronosPipeline", None) or getattr(chronos, "ChronosBoltPipeline")
        return pipeline_class.from_pretrained(asset, device_map=device)
    except Exception as why:                # weights not cached and no hub access, or an API change
        raise Unavailable(f"{asset} could not be loaded here: {type(why).__name__}: {why}")


def _max_steps(context: ForecastContext) -> int:
    series = context.history
    return max(steps_for(weeks, series.frequency, series.offset_days)[0] for weeks in context.horizons)


def _quantile_paths(pipeline, values: Sequence[float], steps: int,
                    quantiles: Sequence[float]) -> dict[float, list[float]]:
    """Ask the pipeline for quantile paths; tolerate the two shapes the chronos package has used."""
    torch = require("torch", for_method="chronos")
    context = torch.tensor([list(values)], dtype=torch.float32)
    if "Chronos2" in type(pipeline).__name__:
        # Chronos-2 takes (batch, n_variates, history) and answers (n_variates, steps, n_quantiles)
        # per series (v0.5.56: found by the first run on Stefan's PC).
        context = context.unsqueeze(1)
    if hasattr(pipeline, "predict_quantiles"):
        out, _mean = pipeline.predict_quantiles(context, prediction_length=steps, quantile_levels=list(quantiles))
        out = out[0]                                        # (steps, n_quantiles), or (1, steps, n_q) for Chronos-2
        if hasattr(out, "dim") and out.dim() == 3:
            out = out[0]
        return {q: [float(out[k][i]) for k in range(steps)] for i, q in enumerate(quantiles)}
    samples = pipeline.predict(context, prediction_length=steps)[0]      # (n_samples, steps)
    samples = samples.float()
    return {q: [float(torch.quantile(samples[:, k], q)) for k in range(steps)] for q in quantiles}


class ChronosAdapter(ForecastModelAdapter):
    """Chronos on the target history. Subclasses set the window and the transform."""

    method_key = "chronos2_univariate"
    version = "2.3.2"
    asset = CHRONOS2
    pipeline_kind = "chronos2"
    context_periods: dict[str, int] | None = None          # None = the whole history
    log_input = False

    def _values(self, context: ForecastContext) -> list[float]:
        values = list(context.history.values)
        if self.context_periods is not None:
            values = values[-self.context_periods[context.history.frequency]:]
        if self.log_input:
            if any(v <= 0 for v in values):
                raise Ineligible("log price needs a strictly positive history")
            values = [math.log(v) for v in values]
        return values

    def _run(self, context: ForecastContext) -> ForecastOutput:
        series = context.history
        if len(series.points) < MIN_CONTEXT[series.frequency]:
            raise Ineligible(f"needs {MIN_CONTEXT[series.frequency]} periods of context, has {len(series.points)}")
        pipeline = _pipeline(self.asset, self.pipeline_kind)
        values = self._values(context)
        steps = _max_steps(context)
        paths = _quantile_paths(pipeline, values, steps, DEFAULT_QUANTILES)
        if self.log_input:
            paths = {q: [math.exp(v) for v in path] for q, path in paths.items()}
        central = paths[0.5]
        return self.succeeded(context, points_from_path(context, central, paths),
                              {"asset": self.asset, "context_periods": len(values), "steps": steps,
                               "log_input": self.log_input})


class Chronos2Univariate(ChronosAdapter):
    method_key = "chronos2_univariate"


class Chronos2Recent36Months(ChronosAdapter):
    method_key = "chronos2_recent_36m"
    context_periods = PERIODS_PER_36_MONTHS


class Chronos2LogPrice(ChronosAdapter):
    method_key = "chronos2_log_price"
    log_input = True


class ChronosBoltTiny(ChronosAdapter):
    method_key = "chronos_bolt_tiny"
    asset = BOLT_TINY
    pipeline_kind = "bolt"


def chronos_adapters() -> list[ForecastModelAdapter]:
    return [Chronos2Univariate(), Chronos2Recent36Months(), Chronos2LogPrice(), ChronosBoltTiny()]
