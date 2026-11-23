"""Every governed method, by its registry key, as a runnable adapter.

The seed (`governance.RESEARCH_CONFIGURATIONS` and the engine's registry) says what methods exist;
this says how each one runs. The two are joined on `method_key`, and `missing_adapters` reports any
seeded method this catalogue cannot run, so a review never discovers that by a KeyError.

The catalogue builds adapters; it does not load any runtime. A foundation adapter imports torch
when it is first asked to forecast, not when it is constructed, so listing the catalogue on the
interactive engine costs nothing and loads nothing.
"""
from __future__ import annotations

from typing import Iterable

from app.forecasting.adapters.arithmetic import EngineModelAdapter
from app.forecasting.adapters.base import ForecastModelAdapter
from app.forecasting.adapters.blends import foundation_blends
from app.forecasting.adapters.chronos import chronos_adapters
from app.forecasting.adapters.conditional_chronos import conditional_chronos_adapters
from app.forecasting.adapters.intelligence import IntelligenceEnhanced
from app.forecasting.adapters.lightgbm import LightGBMRelated
from app.forecasting.adapters.sarimax import SARIMAXRelated
from app.forecasting.adapters.timesfm import TimesFM25
from app.forecasting.registry import ModelRegistry, default_registry


def governed_adapters(registry: ModelRegistry | None = None) -> dict[str, ForecastModelAdapter]:
    registry = registry or default_registry()
    from app.forecasting.adapter import Purpose
    from app.forecasting.models.regression import FeedstockRegression

    adapters: list[ForecastModelAdapter] = [EngineModelAdapter(registry.create(spec.key, purpose=Purpose.RESEARCH))
                                            for spec in registry.specs()]
    # Retired in the engine's registry, INACTIVE in governance; runnable if an administrator reactivates it.
    if "feedstock_regression" not in {a.method_key for a in adapters}:
        adapters.append(EngineModelAdapter(FeedstockRegression()))
    foundation = chronos_adapters() + [TimesFM25()]
    adapters.extend(foundation)
    members = [a for a in foundation if a.method_key in ("chronos2_univariate", "chronos_bolt_tiny", "timesfm_2_5")]
    adapters.extend(foundation_blends(members))
    adapters.extend([LightGBMRelated(), SARIMAXRelated(), *conditional_chronos_adapters(), IntelligenceEnhanced()])
    return {adapter.method_key: adapter for adapter in adapters}


def adapters_for(keys: Iterable[str], registry: ModelRegistry | None = None) -> list[ForecastModelAdapter]:
    catalogue = governed_adapters(registry)
    missing = [key for key in keys if key not in catalogue]
    if missing:
        raise KeyError(f"no adapter for method(s): {', '.join(missing)}")
    return [catalogue[key] for key in keys]


def missing_adapters(method_keys: Iterable[str], registry: ModelRegistry | None = None) -> list[str]:
    catalogue = governed_adapters(registry)
    return sorted(key for key in method_keys if key not in catalogue)
