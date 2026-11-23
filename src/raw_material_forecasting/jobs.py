from __future__ import annotations
from datetime import datetime
from .domain import MarketIdentity
from .model_registry import default_model_registry
from .models.baselines import NaiveAdapter,EtsAdapter
from .models.chronos2 import Chronos2Adapter
from .models.timesfm25 import TimesFm25Adapter
from .orchestrator import ForecastOrchestrator, benchmark_adapter
from .price_history import build_price_history
from .repositories import MarketRepository

CHINA_PMDI_IDENTITY=MarketIdentity("MDI","polymeric MDI","China","China","China domestic benchmark/market avg","UNKNOWN","UNKNOWN","USD")
class StaleMarketStateError(RuntimeError):pass

def default_forecast_adapters(): return (NaiveAdapter(),EtsAdapter(),Chronos2Adapter(),TimesFm25Adapter())

def _weekly_continuity(history)->bool:
    dates=sorted(r["observation_date"] for r in history.observed)
    return len(dates)>1 and all((b-a).days==7 for a,b in zip(dates,dates[1:]))

def issue_china_pmdi_forecast(repo:MarketRepository,*,as_of:datetime,adapters=None,registry=None,benchmarker=benchmark_adapter,max_freshness_days:int=35,regime_count:int=1):
    hist=build_price_history(CHINA_PMDI_IDENTITY,as_of,repo)
    if not hist.observed: raise StaleMarketStateError("no China pMDI observations available")
    latest=max(r["observation_date"] for r in hist.observed)
    freshness=(as_of.date()-latest).days
    if freshness>max_freshness_days: raise StaleMarketStateError(f"latest China pMDI observation is {freshness} days old; maximum for issued forecast is {max_freshness_days}")
    orch=ForecastOrchestrator(repo,registry or default_model_registry(),adapters or default_forecast_adapters(),benchmarker)
    return orch.run(hist,horizons=(4,8,12),regime_count=regime_count,target_frequency_continuous=_weekly_continuity(hist))
