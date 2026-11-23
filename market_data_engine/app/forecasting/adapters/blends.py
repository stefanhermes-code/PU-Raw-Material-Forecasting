"""Blends of foundation models: equal weights, and weights earned on matured outcomes.

A blend is a method like any other: it has a key, a version, and it answers a context or says why
it cannot. It does not paper over a member. If a member is UNAVAILABLE on this machine the blend is
UNAVAILABLE; if a member is INELIGIBLE for this series the blend is INELIGIBLE; if a member FAILED
the blend FAILED and names it. A blend of two out of three would be a different method with a
different record, and it would be one nobody registered.

ADAPTIVE WEIGHTS (plan Task 3, step 5). The adaptive blend weights each member by how it did at
recent origins - and only at origins whose outcome had LANDED by the forecast cutoff. An origin
eight weeks before the cutoff has a matured four-week outcome and an immature eight-week one; the
four-week weight may use it and the eight-week weight may not. Everything the weighting reads is in
`context.history`, which holds nothing after the cutoff, so a later outcome is not merely ignored, it
is not there to be read. The origins used are reported per horizon so a reviewer can check that.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from app.forecasting.adapter import ForecastPoint, steps_for
from app.forecasting.adapters.base import (
    ExecutionStatus, ForecastContext, ForecastModelAdapter, ForecastOutput, Ineligible, Unavailable,
)

DEFAULT_MEMBERS = ("chronos2_univariate", "chronos_bolt_tiny", "timesfm_2_5")
# How many recent origins the adaptive blend learns its weights from, per horizon.
LOOKBACK_ORIGINS = 8
# A member whose matured error is this small relative to the best gets no more weight than this cap
# would give it; keeps one lucky origin from handing a member the whole blend.
MAX_WEIGHT = 0.7


def _member_outputs(members: Sequence[ForecastModelAdapter], context: ForecastContext) -> dict[str, ForecastOutput]:
    outputs = {member.method_key: member.forecast(context) for member in members}
    # A failure is the most serious fact and is reported first (v0.5.60, gate finding 8): a member that
    # FAILED beside one that was INELIGIBLE makes the blend FAILED, not INELIGIBLE.
    for key, output in outputs.items():
        if output.status is ExecutionStatus.FAILED:
            raise RuntimeError(f"member {key} failed: {output.reason}")
    for key, output in outputs.items():
        if output.status is ExecutionStatus.UNAVAILABLE:
            raise Unavailable(f"member {key} is unavailable here: {output.reason}")
    for key, output in outputs.items():
        if output.status is ExecutionStatus.INELIGIBLE:
            raise Ineligible(f"member {key} is ineligible: {output.reason}")
    return outputs


def _blend_points(outputs: dict[str, ForecastOutput], weights: dict[int, dict[str, float]],
                  horizons: Sequence[int]) -> tuple[ForecastPoint, ...]:
    points = []
    for weeks in horizons:
        members = {key: output.at(weeks) for key, output in outputs.items()}
        if any(point is None for point in members.values()):
            continue                                   # unanswered by a member: unanswered by the blend
        w = weights[weeks]
        value = sum(w[key] * point.value for key, point in members.items())
        quantiles = {}
        for q in set.intersection(*(set(point.quantiles) for point in members.values())):
            quantiles[q] = sum(w[key] * point.quantiles[q] for key, point in members.items())
        first = next(iter(members.values()))
        points.append(replace(first, value=value, quantiles=quantiles))
    return tuple(points)


class EqualFoundationBlend(ForecastModelAdapter):
    method_key = "foundation_blend_equal"
    version = "1"

    def __init__(self, members: Sequence[ForecastModelAdapter]) -> None:
        self.members = tuple(members)

    def _run(self, context: ForecastContext) -> ForecastOutput:
        outputs = _member_outputs(self.members, context)
        share = 1.0 / len(outputs)
        weights = {weeks: {key: share for key in outputs} for weeks in context.horizons}
        return self.succeeded(context, _blend_points(outputs, weights, context.horizons),
                              {"members": list(outputs), "weights": {str(h): w for h, w in weights.items()}})


def matured_weights(members: Sequence[ForecastModelAdapter], context: ForecastContext,
                    lookback: int = LOOKBACK_ORIGINS) -> tuple[dict[int, dict[str, float]], dict]:
    """Inverse-MAE weights per horizon from outcomes that had landed by the cutoff.

    Returns (weights, evidence). `evidence[h]["origins"]` lists the origins actually used for
    horizon h; each one's target period is at or before the cutoff, and that is checked here, not
    assumed. With no matured origin at a horizon the weights are equal and the evidence says so.
    """
    series = context.history
    weights: dict[int, dict[str, float]] = {}
    evidence: dict = {}
    keys = [member.method_key for member in members]
    for weeks in context.horizons:
        steps, _exact = steps_for(weeks, series.frequency, series.offset_days)
        errors: dict[str, list[float]] = {key: [] for key in keys}
        used: list[str] = []
        # Walk back from the newest origin whose target has landed. The origin at index i answers
        # for index i + steps, which must exist in the history as it stood at the cutoff.
        last = len(series.points) - 1
        origin_index = last - steps
        while origin_index >= 0 and len(used) < lookback:
            origin_day, _ = series.points[origin_index]
            target_day, truth = series.points[origin_index + steps]
            if target_day > context.cutoff:                    # cannot happen by construction; asserted anyway
                origin_index -= 1
                continue
            window = series.up_to(origin_day)
            sub = ForecastContext(context.identity_key, context.chemical, origin_day, window, (weeks,),
                                  tuple(s.up_to(origin_day) for s in context.related),
                                  tuple(s.up_to(origin_day) for s in context.features),
                                  context.known_future, context.config)
            answers = {member.method_key: member.forecast(sub) for member in members}
            if all(a.succeeded and a.at(weeks) is not None for a in answers.values()):
                for key, answer in answers.items():
                    errors[key].append(abs(truth - answer.at(weeks).value))
                used.append(origin_day.isoformat())
            origin_index -= 1
        if used:
            mae = {key: sum(e) / len(e) for key, e in errors.items()}
            floor = max(1e-9, min(mae.values()) * 1e-3)
            inverse = {key: 1.0 / max(value, floor) for key, value in mae.items()}
            total = sum(inverse.values())
            raw = {key: value / total for key, value in inverse.items()}
            weights[weeks] = _capped(raw, MAX_WEIGHT)
            evidence[weeks] = {"origins": used, "mae": {k: round(v, 4) for k, v in mae.items()}}
        else:
            weights[weeks] = {key: 1.0 / len(keys) for key in keys}
            evidence[weeks] = {"origins": [], "note": "no matured outcome by the cutoff; equal weights"}
    return weights, evidence


def _capped(raw: dict[str, float], cap: float) -> dict[str, float]:
    """No member above `cap`; the excess goes to the others in proportion. With two members at a
    0.7 cap the stronger gets 0.7 and the weaker 0.3, however lopsided the errors were - one lucky
    stretch of origins must not hand a member the whole blend."""
    if len(raw) * cap <= 1.0:
        return {key: 1.0 / len(raw) for key in raw}
    out = dict(raw)
    for _ in range(len(raw)):
        over = {k for k, v in out.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(out[k] - cap for k in over)
        for k in over:
            out[k] = cap
        free = {k: v for k, v in out.items() if k not in over and v < cap}
        total = sum(free.values())
        for k, v in free.items():
            out[k] = v + (excess * (v / total) if total else excess / len(free))
    return out


class AdaptiveFoundationBlend(ForecastModelAdapter):
    method_key = "foundation_blend_adaptive"
    version = "1"

    def __init__(self, members: Sequence[ForecastModelAdapter], lookback: int = LOOKBACK_ORIGINS) -> None:
        self.members = tuple(members)
        self.lookback = lookback

    def _run(self, context: ForecastContext) -> ForecastOutput:
        outputs = _member_outputs(self.members, context)
        weights, evidence = matured_weights(self.members, context, self.lookback)
        return self.succeeded(context, _blend_points(outputs, weights, context.horizons),
                              {"members": list(outputs),
                               "weights": {str(h): {k: round(v, 4) for k, v in w.items()} for h, w in weights.items()},
                               "evidence": {str(h): e for h, e in evidence.items()}})


def foundation_blends(members: Sequence[ForecastModelAdapter]) -> list[ForecastModelAdapter]:
    return [EqualFoundationBlend(members), AdaptiveFoundationBlend(members)]
