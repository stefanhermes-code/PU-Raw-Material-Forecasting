from __future__ import annotations
import numpy as np
import pandas as pd
from .base import ForecastRequest, ForecastResult, ModelRuntimeUnavailable

class TimesFm25Adapter:
    model_key="timesfm25"; model_version="google/timesfm-2.5-200m-pytorch"
    def __init__(self,model=None): self.model=model
    def _model(self):
        if self.model is not None: return self.model
        try:
            import timesfm
            return timesfm.TimesFm_2p5_200M_torch.from_pretrained(self.model_version)
        except Exception as exc: raise ModelRuntimeUnavailable(f"TimesFM 2.5 runtime unavailable: {exc}") from exc
    def forecast(self,request:ForecastRequest)->ForecastResult:
        model=self._model()
        point, quantile = model.forecast(horizon=request.horizon, inputs=[request.history.to_numpy(dtype=float)])
        point=np.asarray(point)[0]
        qarr=np.asarray(quantile)[0]
        # Expected native layout: horizon x (mean + q10..q90). Some runtimes omit the mean column.
        if qarr.ndim != 2: raise ModelRuntimeUnavailable("Unexpected TimesFM quantile output shape")
        deciles=[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9]
        offset=1 if qarr.shape[1] >= 10 else 0
        if qarr.shape[1] < offset+9: raise ModelRuntimeUnavailable("TimesFM quantile head does not expose native deciles")
        qmap={q:pd.Series(qarr[:,offset+i],dtype=float) for i,q in enumerate(deciles)}
        # Use the model's native q50 as the central/median forecast. Keep the point forecast only as metadata.
        meta={"probabilistic":True,"native_quantiles":"deciles","point_forecast_role":"model point/mean, not relabelled as median","point_forecast":point.tolist()}
        return ForecastResult(self.model_key,self.model_version,qmap,meta)
