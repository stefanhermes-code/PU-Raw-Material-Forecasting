"""The engine's own methods behind the governed contract.

Nothing is reimplemented. `EngineModelAdapter` holds one `ForecastModel` from the engine registry
(`app.forecasting.registry`) and translates: a context becomes the model's request, the model's
result becomes an output, a declined result (no points, a reason) becomes INELIGIBLE, an exception
becomes FAILED. The two "arithmetic" configurations the CR names are the registry's `weighted_error`
(the existing weighted method) and `momentum` (the current momentum method); the controls and ETS
come through the same door so the tournament marks everything on one exam.

Method keys are the engine's model keys, which are also the `method_key`s the governance seed wrote
(`governance._engine_entries`), so a tournament result joins the registry without a translation table.
"""
from __future__ import annotations

from typing import Iterable

from app.forecasting.adapter import ForecastModel, Purpose
from app.forecasting.adapters.base import ForecastContext, ForecastModelAdapter, ForecastOutput
from app.forecasting.domain import FLAT_CONTROL_KEYS
from app.forecasting.registry import ModelRegistry, default_registry


class EngineModelAdapter(ForecastModelAdapter):
    def __init__(self, model: ForecastModel) -> None:
        self.model = model
        self.method_key = model.spec.key
        self.version = model.spec.version
        self.kind = "CONTROL" if model.spec.key in FLAT_CONTROL_KEYS else "UNIVERSAL"

    def _run(self, context: ForecastContext) -> ForecastOutput:
        # The engine's models take covariates only when they say so; a model handed covariates it
        # does not read ignores them, so the context's related series are passed as-is.
        result = self.model.forecast(context.request(
            covariates=context.related if self.model.spec.supports_covariates else ()))
        return self.from_result(context, result)


def engine_adapters(registry: ModelRegistry | None = None, *, keys: Iterable[str] | None = None,
                    purpose: Purpose = Purpose.RESEARCH) -> list[EngineModelAdapter]:
    """One adapter per registered engine model (or per key asked), research purpose by default.

    RESEARCH so a research-only licence is benchmarked rather than hidden; what a licence permits to
    SHIP is the registry's business and the governance table's `commercial_use`, not the tournament's.
    """
    registry = registry or default_registry()
    chosen = list(keys) if keys is not None else [spec.key for spec in registry.candidates(purpose=purpose)]
    return [EngineModelAdapter(registry.create(key, purpose=purpose)) for key in chosen]
