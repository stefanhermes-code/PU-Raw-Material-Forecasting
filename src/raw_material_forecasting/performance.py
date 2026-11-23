from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json
import numpy as np
from .domain import ForecastOutcome, PriceObservation
from .repositories import MarketRepository

@dataclass(frozen=True)
class PerformanceSummary:
    identity_key:str; horizon_weeks:int; forecast_count:int; mae:float; bias:float; mape:float; direction_accuracy:float; interval_coverage:float

def _reference_price(vintage)->float|None:
    try: return float(json.loads(vintage.evidence_snapshot_json).get("latest_observation_value"))
    except Exception: return None

def match_outcomes(new_observation: PriceObservation, repo: MarketRepository, *, scored_at:datetime|None=None)->tuple[ForecastOutcome,...]:
    scored_at=scored_at or datetime.now(timezone.utc); out=[]
    for v in repo.list_forecast_vintages(new_observation.identity.key):
        if v.target_date != new_observation.observation_date or repo.forecast_outcome_exists(v.vintage_id): continue
        actual=float(new_observation.value); signed=float(v.p50)-actual; ref=_reference_price(v)
        direction=None if ref is None else bool(np.sign(float(v.p50)-ref)==np.sign(actual-ref))
        covered=None if v.p10 is None or v.p90 is None else bool(float(v.p10)<=actual<=float(v.p90))
        pct=None if actual==0 else abs(signed)/abs(actual)*100.0
        oid=hashlib.sha256(f"{v.vintage_id}|{new_observation.observation_date}|{actual}".encode()).hexdigest()[:24]
        o=ForecastOutcome(oid,v.vintage_id,v.identity_key,v.horizon_weeks,v.target_date,new_observation.observation_date,actual,float(v.p50),abs(signed),signed,pct,direction,covered,scored_at)
        if repo.insert_forecast_outcome(o): out.append(o)
    return tuple(out)

def performance_summary(identity_key:str,horizon_weeks:int,repo:MarketRepository)->PerformanceSummary|None:
    rows=[r for r in repo.list_forecast_outcomes(identity_key) if r.horizon_weeks==horizon_weeks]
    if not rows:return None
    dirs=[float(r.direction_correct) for r in rows if r.direction_correct is not None]
    cov=[float(r.interval_covered) for r in rows if r.interval_covered is not None]
    return PerformanceSummary(identity_key,horizon_weeks,len(rows),float(np.mean([r.absolute_error for r in rows])),float(np.mean([r.signed_error for r in rows])),float(np.mean([r.percentage_error for r in rows if r.percentage_error is not None])),float(np.mean(dirs)) if dirs else float("nan"),float(np.mean(cov)) if cov else float("nan"))
