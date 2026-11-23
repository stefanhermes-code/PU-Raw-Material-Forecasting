"""Which models exist, and what each is allowed to do.

The registry is the architecture's answer to vendor lock-in: models are entries, not code paths, and
the orchestrator asks the registry what it may use rather than importing a favourite. It is also
where the licence gate lives, because a licence has to be checked before a model runs and not after
it has won a benchmark - the temptation to promote something that scored well is exactly what a gate
is for.

A research-only model is never hidden. It is benchmarked, its score is reported, and it cannot
produce a forecast anybody sees. That is a deliberately different thing from excluding it: knowing
that an unusable model beats the champion is useful, and pretending it does not exist is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from app.forecasting.adapter import (
    ForecastModel, LicenceClass, LicenceRefused, ModelSpec, Purpose,
)


@dataclass(frozen=True)
class Registration:
    spec: ModelSpec
    factory: Callable[[], ForecastModel]


class ModelRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, Registration] = {}

    def register(self, spec: ModelSpec, factory: Callable[[], ForecastModel]) -> None:
        if spec.key in self._entries:
            raise ValueError(f"a model is already registered under {spec.key!r}")
        self._entries[spec.key] = Registration(spec, factory)

    def specs(self) -> list[ModelSpec]:
        return [entry.spec for entry in self._entries.values()]

    def spec(self, key: str) -> ModelSpec:
        return self._entries[key].spec

    def create(self, key: str, *, purpose: Purpose = Purpose.RESEARCH) -> ForecastModel:
        """Build a model, refusing outright when the purpose exceeds its licence."""
        entry = self._entries[key]
        licence_gate(entry.spec, purpose)
        return entry.factory()

    def candidates(self, *, purpose: Purpose, include_controls: bool = True) -> list[ModelSpec]:
        """Every model eligible for this purpose.

        Controls are included by default because a champion that has not been compared with "carry
        yesterday's price forward" has not been shown to be worth anything.
        """
        out = []
        for entry in self._entries.values():
            if not include_controls and entry.spec.is_control:
                continue
            if purpose is Purpose.COMMERCIAL and not entry.spec.commercial:
                continue
            out.append(entry.spec)
        return sorted(out, key=lambda spec: (not spec.is_control, spec.key))

    def controls(self) -> list[ModelSpec]:
        return [spec for spec in self.specs() if spec.is_control]


def licence_gate(spec: ModelSpec, purpose: Purpose) -> None:
    """Refuse before running, with the reason named.

    An unverified licence is treated as research-only rather than as permission. The default for an
    unanswered question is no.
    """
    if purpose is not Purpose.COMMERCIAL:
        return
    if spec.licence_class is LicenceClass.COMMERCIAL:
        return
    reason = ("its licence has not been verified" if spec.licence_class is LicenceClass.UNVERIFIED
              else f"its licence ({spec.licence}) does not permit commercial use")
    raise LicenceRefused(
        f"{spec.key} may not produce a commercial forecast: {reason}. It remains available for "
        "research and benchmarking."
    )


def default_registry() -> ModelRegistry:
    """The controls, always present, and the candidates earning a record.

    Naive and ETS are permanent challengers rather than a stage to pass through. A forecast that
    cannot beat carrying yesterday's price forward is not a forecast, and the only way that stays
    true is if the comparison is always available and never optional.

    A candidate is here because it has cleared the controls on SOME history and has to earn its
    record on this one. Momentum beat both controls at one month on 151 months of European TDI; the
    screen shows China, where 28 months cannot tell a 12% improvement from noise. Registering it
    means every forecast it gives is written to the vintage store beside the controls, so the
    question gets answered by forecasts that were actually made. Which model the cards show is a
    separate decision, made in the application, and it does not change because something new appears
    here.

    The feedstock regression is deliberately absent. It lost to naive on the same 102 origins, and
    losing to naive is the one thing that keeps a model out regardless of anything else about it.
    """
    from app.forecasting.models.ets import ExponentialSmoothing
    from app.forecasting.models.momentum import Momentum
    from app.forecasting.models.naive import NaiveLast, SeasonalNaive
    from app.forecasting.models.weighted_error import WeightedError

    registry = ModelRegistry()
    # WeightedError answers Europe TDI, pMDI and polyol only, and declines everything else.
    for model_class in (NaiveLast, SeasonalNaive, ExponentialSmoothing, Momentum, WeightedError):
        registry.register(model_class.SPEC, model_class)
    return registry


def register_frontier(registry: ModelRegistry, specs: Iterable[ModelSpec],
                      factory_for: Callable[[ModelSpec], Callable[[], ForecastModel]]) -> None:
    """Add foundation-model candidates without the registry importing any of them."""
    for spec in specs:
        registry.register(spec, factory_for(spec))
