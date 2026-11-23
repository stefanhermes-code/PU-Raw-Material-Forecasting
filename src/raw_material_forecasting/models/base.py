from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping, Protocol
import pandas as pd

class ModelRuntimeUnavailable(RuntimeError): pass

@dataclass(frozen=True)
class ForecastRequest:
    history: pd.Series
    horizon: int
    quantiles: tuple[float,...]=(0.1,0.25,0.5,0.75,0.9)
    covariates: pd.DataFrame | None=None
    known_future: pd.DataFrame | None=None
    as_of: object | None=None
    def __post_init__(self):
        if self.horizon<=0: raise ValueError("horizon must be positive")
        if len(self.history)==0: raise ValueError("history must not be empty")
        if not isinstance(self.history.index,pd.DatetimeIndex): raise ValueError("history must use a DatetimeIndex")
        if any(q<=0 or q>=1 for q in self.quantiles): raise ValueError("quantiles must be between 0 and 1")

@dataclass(frozen=True)
class ForecastResult:
    model_key: str
    model_version: str
    quantiles: Mapping[float,pd.Series]
    metadata: Mapping[str,object]=field(default_factory=dict)
    def __post_init__(self):
        if 0.5 not in self.quantiles: raise ValueError("0.5 quantile is required as central forecast")
        lengths={len(v) for v in self.quantiles.values()}
        if len(lengths)!=1: raise ValueError("all forecast quantiles must have equal length")
    @property
    def central(self)->pd.Series:
        return pd.Series(self.quantiles[0.5],dtype=float).reset_index(drop=True)

class ForecastAdapter(Protocol):
    model_key: str
    model_version: str
    def forecast(self,request:ForecastRequest)->ForecastResult: ...
