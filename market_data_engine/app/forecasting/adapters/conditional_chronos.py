"""Chronos-2 with a related market as covariate, and Chronos-2 on the joint chemical network.

Both conditional, both Challengers, both UNAVAILABLE without the chronos runtime and INELIGIBLE
without a related series visible at the cutoff. The related-market configuration hands Chronos-2
one covariate - the strongest related series the cutoff-safe ranking found; the joint-network
configuration forecasts the target together with every related series in the chemical network, as
one multivariate problem, and keeps the target's own quantiles.

Chronos-2's covariate interface takes one record per series with a `target` and, optionally,
`past_covariates`. The covariate is aligned to the target's periods and cut at the target's last
period; a covariate without a value at that period makes the method INELIGIBLE at this origin
rather than filled forward, because filling it forward would hand the model a value it did not
have. The exact call signature has moved between chronos-forecasting releases; the adapter tries
the 2.3 form and reports FAILED, with the error, if the installed release differs - never a quiet
fallback to the univariate answer, which is a different registered method.
"""
from __future__ import annotations

from typing import Sequence

from app.forecasting.adapter import DEFAULT_QUANTILES, Series, steps_for
from app.forecasting.adapters.base import ForecastContext, ForecastOutput, Ineligible, points_from_path, require
from app.forecasting.adapters.chronos import CHRONOS2, MIN_CONTEXT, _pipeline
from app.forecasting.adapters.conditional import ConditionalAdapter, MIN_RELATED_OVERLAP


def _aligned(target: Series, related: Sequence[Series]) -> tuple[list[float], list[list[float]]]:
    """The most recent unbroken run of target periods at which every related series has a value.

    v0.5.58: this used to keep every matching period and then demand that they were ALL of the target's
    periods, so one early hole in a related feed (or a related history that starts later than the
    target's) made the method INELIGIBLE everywhere. Chronos-2 needs a contiguous context ending at the
    target's last period, not the whole history; the run is taken backwards from the last period and
    stops at the first period any series lacks.
    """
    columns = [dict(s.points) for s in related]
    values, covariates = [], []
    for day, value in reversed(target.points):
        if any(day not in col for col in columns):
            break
        values.append(float(value))
        covariates.append([float(col[day]) for col in columns])
    values.reverse()
    covariates.reverse()
    return values, covariates


def _quantile_paths_with_covariates(pipeline, values: list[float], covariates: list[list[float]], steps: int,
                                    quantiles: Sequence[float]) -> dict[float, list[float]]:
    torch = require("torch", for_method=CHRONOS2)
    record = {"target": torch.tensor(values, dtype=torch.float32)}
    if covariates:
        record["past_covariates"] = {f"cov_{i}": torch.tensor([row[i] for row in covariates], dtype=torch.float32)
                                     for i in range(len(covariates[0]))}
    out, _mean = pipeline.predict_quantiles([record], prediction_length=steps, quantile_levels=list(quantiles))
    out = out[0]                                            # (1, steps, n_quantiles) for a 1-d target
    if hasattr(out, "dim") and out.dim() == 3:
        out = out[0]
    return {q: [float(out[k][i]) for k in range(steps)] for i, q in enumerate(quantiles)}


class Chronos2RelatedMarket(ConditionalAdapter):
    method_key = "chronos2_related_market"
    version = "2.3.2/2"   # chronos 2.3.2; /2: covariates at their publication lag, recent unbroken run (v0.5.60)
    min_related = 1

    def _run(self, context: ForecastContext) -> ForecastOutput:
        related = self.related(context)[:1]
        series = context.history
        if len(series.points) < MIN_CONTEXT[series.frequency]:
            raise Ineligible(f"needs {MIN_CONTEXT[series.frequency]} periods of context, has {len(series.points)}")
        values, covariates = _aligned(series, related)
        if len(values) < MIN_RELATED_OVERLAP:
            raise Ineligible(f"the related series {related[0].key} shares only {len(values)} unbroken recent periods "
                             f"with the target at the cutoff; {MIN_RELATED_OVERLAP} needed")
        pipeline = _pipeline(CHRONOS2, "chronos2")
        steps = max(steps_for(weeks, series.frequency, series.offset_days)[0] for weeks in context.horizons)
        paths = _quantile_paths_with_covariates(pipeline, values, covariates, steps, DEFAULT_QUANTILES)
        return self.succeeded(context, points_from_path(context, paths[0.5], paths),
                              {"asset": CHRONOS2, "covariate": related[0].key, "steps": steps})


class Chronos2JointNetwork(ConditionalAdapter):
    method_key = "chronos2_joint_network"
    version = "2.3.2/2"   # chronos 2.3.2; /2: covariates at their publication lag, recent unbroken run (v0.5.60)
    min_related = 2

    def _run(self, context: ForecastContext) -> ForecastOutput:
        related = self.related(context)
        series = context.history
        if len(series.points) < MIN_CONTEXT[series.frequency]:
            raise Ineligible(f"needs {MIN_CONTEXT[series.frequency]} periods of context, has {len(series.points)}")
        values, others = _aligned(series, related)
        if len(values) < MIN_RELATED_OVERLAP:
            raise Ineligible(f"the network shares only {len(values)} unbroken recent periods with the target "
                             f"at the cutoff; {MIN_RELATED_OVERLAP} needed")
        pipeline = _pipeline(CHRONOS2, "chronos2")
        torch = require("torch", for_method=CHRONOS2)
        steps = max(steps_for(weeks, series.frequency, series.offset_days)[0] for weeks in context.horizons)
        # One multivariate target: the market first, then its network, forecast together.
        joint = torch.tensor([values, *[[row[i] for row in others] for i in range(len(related))]], dtype=torch.float32)
        out, _mean = pipeline.predict_quantiles([{"target": joint}], prediction_length=steps,
                                                quantile_levels=list(DEFAULT_QUANTILES))
        out = out[0]                                            # (n_targets, steps, n_quantiles) or (steps, n_q)
        if hasattr(out, "dim") and out.dim() == 3:
            out = out[0]
        paths = {q: [float(out[k][i]) for k in range(steps)] for i, q in enumerate(DEFAULT_QUANTILES)}
        return self.succeeded(context, points_from_path(context, paths[0.5], paths),
                              {"asset": CHRONOS2, "network": [s.key for s in related], "steps": steps})


def conditional_chronos_adapters() -> list[ConditionalAdapter]:
    return [Chronos2RelatedMarket(), Chronos2JointNetwork()]
