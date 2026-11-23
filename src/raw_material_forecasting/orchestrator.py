from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import math
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from .contracts import SourceRights
from .domain import ForecastVintage
from .model_registry import ModelRegistry
from .models.base import ForecastAdapter, ForecastRequest, ModelRuntimeUnavailable
from .models.baselines import NaiveAdapter
from .price_history import PriceHistory
from .repositories import MarketRepository
from .series_audit import evaluate_horizon_gate


@dataclass(frozen=True)
class BenchmarkEvidence:
    model_key: str
    mae: float
    directional_accuracy: float
    independent_episodes: int
    significance_passed: bool
    p_value_vs_naive: float | None = None
    improvement_vs_naive_pct: float | None = None


@dataclass(frozen=True)
class HorizonForecast:
    horizon_weeks: int
    vintage_id: str
    model_key: str
    model_version: str
    release_state: str
    gate_reasons: tuple[str,...]
    p10: float | None
    p25: float | None
    p50: float
    p75: float | None
    p90: float | None
    benchmark: BenchmarkEvidence


@dataclass(frozen=True)
class ForecastRunResult:
    identity_key: str
    as_of: object
    horizons: tuple[HorizonForecast,...]
    skipped_models: tuple[str,...]


def _history_series(history: PriceHistory) -> pd.Series:
    frame=pd.DataFrame([{"date":r["observation_date"],"value":r["value"]} for r in history.observed])
    if frame.empty: return pd.Series(dtype=float,index=pd.DatetimeIndex([]))
    frame["date"]=pd.to_datetime(frame["date"])
    frame=frame.sort_values("date").drop_duplicates("date",keep="last")
    return pd.Series(frame.value.to_numpy(dtype=float),index=pd.DatetimeIndex(frame.date))


def _significance(errors: list[float], naive_errors: list[float]) -> tuple[bool,float|None]:
    if len(errors)<10: return False,None
    dif=np.asarray(naive_errors)-np.asarray(errors)
    sd=float(np.std(dif,ddof=1))
    if sd==0: return (float(np.mean(dif))>0),0.0 if float(np.mean(dif))>0 else 1.0
    z=float(np.mean(dif))/(sd/math.sqrt(len(dif)))
    p=math.erfc(abs(z)/math.sqrt(2.0))
    return bool(z>0 and p<0.05),p


def benchmark_adapter(adapter: ForecastAdapter, history: pd.Series, horizon: int, *, min_context: int=16) -> BenchmarkEvidence:
    naive=NaiveAdapter()
    model_errors=[]; naive_errors=[]; direction=[]
    origins=range(min_context-1, max(min_context-1,len(history)-horizon), horizon)
    for origin in origins:
        if origin+horizon>=len(history): continue
        train=history.iloc[:origin+1]
        actual=float(history.iloc[origin+horizon]); base=float(train.iloc[-1])
        req=ForecastRequest(train,horizon,(0.5,))
        try: m=float(adapter.forecast(req).central.iloc[-1])
        except Exception: continue
        n=float(naive.forecast(req).central.iloc[-1])
        model_errors.append(abs(actual-m)); naive_errors.append(abs(actual-n))
        direction.append(float(np.sign(m-base)==np.sign(actual-base)))
    if not model_errors:
        return BenchmarkEvidence(adapter.model_key,float("inf"),0.0,0,False,None,None)
    mae=float(np.mean(model_errors)); nmae=float(np.mean(naive_errors))
    uplift=0.0 if nmae==0 else (nmae-mae)/nmae*100.0
    passed,p=_significance(model_errors,naive_errors)
    return BenchmarkEvidence(adapter.model_key,mae,float(np.mean(direction)),len(model_errors),passed,p,float(uplift))


def _endpoint(result, q: float) -> float | None:
    series=result.quantiles.get(q)
    return None if series is None else float(pd.Series(series).iloc[-1])


class ForecastOrchestrator:
    def __init__(self, repo: MarketRepository, registry: ModelRegistry, adapters: Iterable[ForecastAdapter], benchmarker: Callable=benchmark_adapter):
        self.repo=repo; self.registry=registry; self.adapters={a.model_key:a for a in adapters}; self.benchmarker=benchmarker

    def run(self, history: PriceHistory, *, horizons=(4,8,12), regime_count: int=1, target_frequency_continuous: bool=False) -> ForecastRunResult:
        series=_history_series(history)
        skipped=[]; forecasts=[]
        sources=[self.repo.get_source(r["source_key"]) for r in history.observed]
        rights_clear=bool(sources) and all(s is not None and SourceRights(s.rights_class).eligible_for_operational for s in sources)
        publication_verified=bool(history.observed) and all(bool(r.get("publication_date_verified")) for r in history.observed)
        records={r.model_key:r for r in self.registry.commercial_candidates()}
        for horizon in horizons:
            evaluated=[]
            for key,adapter in self.adapters.items():
                if key not in records: continue
                try:
                    evidence=self.benchmarker(adapter,series,horizon)
                    evaluated.append((evidence.mae,key,evidence,adapter))
                except ModelRuntimeUnavailable as exc:
                    skipped.append(f"{key}: {exc}")
                except Exception as exc:
                    skipped.append(f"{key}: benchmark failed: {exc}")
            if not evaluated: raise RuntimeError(f"no runnable commercial model for {horizon} weeks")
            evaluated.sort(key=lambda x:(math.isinf(x[0]),x[0],x[1]))
            chosen=None
            for _mae,key,evidence,adapter in evaluated:
                try:
                    requested=(0.1,0.25,0.5,0.75,0.9) if records[key].quantile_support else (0.5,)
                    result=adapter.forecast(ForecastRequest(series,horizon,requested))
                    chosen=(key,evidence,result); break
                except ModelRuntimeUnavailable as exc: skipped.append(f"{key}: {exc}")
                except Exception as exc: skipped.append(f"{key}: forecast failed: {exc}")
            if chosen is None: raise RuntimeError(f"no runnable forecast for {horizon} weeks")
            key,evidence,result=chosen
            gate=evaluate_horizon_gate(
                observation_count=history.real_observation_count,
                context_observations=min(24,history.real_observation_count),
                horizon_observations=horizon,
                regime_count=regime_count,
                publication_lag_verified=publication_verified,
                significance_passed=evidence.significance_passed,
                operational_rights_clear=rights_clear,
                target_frequency_continuous=target_frequency_continuous,
            )
            snapshot={
                "identity_key":history.identity.key,"as_of":history.as_of.isoformat(),
                "real_observation_count":history.real_observation_count,"synthetic_observation_count":history.synthetic_observation_count,
                "publication_lag_verified":publication_verified,"operational_rights_clear":rights_clear,
                "target_frequency_continuous":target_frequency_continuous,"regime_count":regime_count,
                "benchmark":evidence.__dict__,"gate_reasons":list(gate.reasons),"model_metadata":dict(result.metadata),
                "latest_observation_value":float(series.iloc[-1]),"latest_observation_date":series.index[-1].date().isoformat(),
            }
            raw=f"{history.identity.key}|{history.as_of.isoformat()}|{horizon}|{key}|{result.model_version}"
            vintage_id=hashlib.sha256(raw.encode()).hexdigest()[:24]
            target_date=(history.as_of+timedelta(weeks=horizon)).date()
            v=ForecastVintage(vintage_id,history.identity.key,history.as_of,horizon,_endpoint(result,0.1),_endpoint(result,0.25),_endpoint(result,0.5),_endpoint(result,0.75),_endpoint(result,0.9),gate.status,key,result.model_version,json.dumps(snapshot,sort_keys=True,default=str),target_date)
            self.repo.insert_forecast_vintage(v)
            forecasts.append(HorizonForecast(horizon,vintage_id,key,result.model_version,gate.status,gate.reasons,v.p10,v.p25,v.p50,v.p75,v.p90,evidence))
        return ForecastRunResult(history.identity.key,history.as_of,tuple(forecasts),tuple(skipped))
