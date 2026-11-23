from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class LagResult:
    lag_weeks: int
    pearson: float
    spearman: float
    evidence_count: int

@dataclass(frozen=True)
class RelationshipProfile:
    from_series: str
    to_series: str
    lag_weeks: int
    pearson: float
    spearman: float
    rolling_stability: float
    forecast_uplift_pct: float
    evidence_count: int
    regime_label: str
    validation_state: str


def _aligned(target: pd.Series, candidate: pd.Series, lag_weeks: int) -> pd.DataFrame:
    frame = pd.concat({"target": pd.Series(target, dtype=float), "candidate": pd.Series(candidate, dtype=float).shift(lag_weeks)}, axis=1).dropna()
    return frame


def scan_lags(target: pd.Series, candidate: pd.Series, lags=(0,1,2,3,4,6,8)) -> tuple[LagResult, ...]:
    out=[]
    for lag in lags:
        if lag < 0: raise ValueError("lags must be non-negative")
        f=_aligned(target,candidate,lag)
        if len(f)<3: out.append(LagResult(lag,0.0,0.0,len(f))); continue
        out.append(LagResult(lag,float(f.target.corr(f.candidate,method="pearson")),float(f.target.corr(f.candidate,method="spearman")),len(f)))
    return tuple(out)


def _rolling_stability(frame: pd.DataFrame, window: int = 12) -> float:
    if len(frame) < max(window, 6): return 0.0
    cors=[]
    for i in range(window, len(frame)+1):
        c=frame.iloc[i-window:i].target.corr(frame.iloc[i-window:i].candidate)
        if pd.notna(c): cors.append(float(c))
    if not cors: return 0.0
    sign=np.sign(np.nanmedian(cors))
    return float(np.mean([np.sign(c)==sign and abs(c)>=0.2 for c in cors]))


def validate_relationship(*, from_series: str, to_series: str, target: pd.Series, candidate: pd.Series, forecast_uplift_pct: float, regime_label: str="ALL") -> RelationshipProfile:
    best=max(scan_lags(target,candidate), key=lambda r: abs(r.pearson))
    frame=_aligned(target,candidate,best.lag_weeks)
    stability=_rolling_stability(frame)
    state="VALIDATED" if (best.evidence_count>=24 and abs(best.pearson)>=0.30 and stability>=0.50 and forecast_uplift_pct>0) else "CANDIDATE"
    return RelationshipProfile(from_series,to_series,best.lag_weeks,best.pearson,best.spearman,stability,float(forecast_uplift_pct),best.evidence_count,regime_label,state)
