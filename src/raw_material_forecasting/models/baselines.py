from __future__ import annotations
import pandas as pd
from .base import ForecastRequest, ForecastResult
from ..forecasting import naive_forecast, ets_forecast

class NaiveAdapter:
    model_key="naive"; model_version="1"
    def forecast(self,request:ForecastRequest)->ForecastResult:
        s=naive_forecast(request.history.reset_index(drop=True),request.horizon)
        return ForecastResult(self.model_key,self.model_version,{0.5:s},{"probabilistic":False,"hidden_control":True})

class EtsAdapter:
    model_key="ets"; model_version="additive-trend"
    def forecast(self,request:ForecastRequest)->ForecastResult:
        s=ets_forecast(request.history.reset_index(drop=True),request.horizon)
        return ForecastResult(self.model_key,self.model_version,{0.5:s},{"probabilistic":False,"hidden_control":True})
