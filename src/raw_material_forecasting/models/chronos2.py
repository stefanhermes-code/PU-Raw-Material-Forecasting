from __future__ import annotations
import pandas as pd
from .base import ForecastRequest, ForecastResult, ModelRuntimeUnavailable

class Chronos2Adapter:
    model_key="chronos2"; model_version="amazon/chronos-2"
    def __init__(self,pipeline=None): self.pipeline=pipeline
    def _pipeline(self):
        if self.pipeline is not None: return self.pipeline
        try:
            from chronos import Chronos2Pipeline
            return Chronos2Pipeline.from_pretrained(self.model_version)
        except Exception as exc: raise ModelRuntimeUnavailable(f"Chronos-2 runtime unavailable: {exc}") from exc
    def forecast(self,request:ForecastRequest)->ForecastResult:
        pipe=self._pipeline()
        frame=pd.DataFrame({"id":"series","timestamp":request.history.index,"target":request.history.to_numpy(dtype=float)})
        kwargs={"data":frame,"prediction_length":request.horizon,"quantile_levels":list(request.quantiles),"id_column":"id","timestamp_column":"timestamp","target":"target"}
        try: out=pipe.predict_df(**kwargs)
        except TypeError:
            kwargs.pop("target",None); out=pipe.predict_df(**kwargs)
        qmap={}
        for q in request.quantiles:
            candidates=[str(q),f"{q:.1f}",f"q{int(round(q*100))}",f"quantile_{q}"]
            col=next((c for c in candidates if c in out.columns),None)
            if col is not None: qmap[float(q)]=pd.Series(out[col].to_numpy(dtype=float))
        if 0.5 not in qmap:
            for c in ("prediction","mean","median"):
                if c in out.columns: qmap[0.5]=pd.Series(out[c].to_numpy(dtype=float)); break
        if 0.5 not in qmap: raise ModelRuntimeUnavailable("Chronos-2 response did not expose a central forecast")
        return ForecastResult(self.model_key,self.model_version,qmap,{"probabilistic":len(qmap)>1})
