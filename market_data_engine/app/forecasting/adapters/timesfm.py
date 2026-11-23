"""TimesFM 2.5 (200M, PyTorch) behind the governed contract.

The research used `google/timesfm-2.5-200m-pytorch` at commit e51928e2 of the timesfm repository,
and the registry's seed records that. TimesFM 3.0 is NOT this method: its weights arrived under a
different licence, and the CR names 2.5 on purpose. The adapter therefore asks for the 2.5 asset by
name and nothing else; a machine that has only another version installed reports UNAVAILABLE.

Like the Chronos adapters: optional runtime, loaded once per process, UNAVAILABLE in words when it
is not here, never on the page path.
"""
from __future__ import annotations

from functools import lru_cache

from app.forecasting.adapter import DEFAULT_QUANTILES, DAILY, MONTHLY, WEEKLY, steps_for
from app.forecasting.adapters.base import (
    ForecastContext, ForecastModelAdapter, ForecastOutput, Ineligible, Unavailable, points_from_path, require,
)

TIMESFM_2_5 = "google/timesfm-2.5-200m-pytorch"
RESEARCH_COMMIT = "e51928e27119cb17bebc005be2696b75e0a9e688"
MIN_CONTEXT = {DAILY: 60, WEEKLY: 16, MONTHLY: 8}


@lru_cache(maxsize=1)
def _model():
    timesfm = require("timesfm", for_method=TIMESFM_2_5,
                      hint=f"pip install git+https://github.com/google-research/timesfm@{RESEARCH_COMMIT}")
    require("torch", for_method=TIMESFM_2_5)
    try:
        loader = getattr(timesfm, "TimesFM_2p5_200M_torch", None)
        if loader is None:
            raise Unavailable(f"the installed timesfm has no TimesFM 2.5 loader; {TIMESFM_2_5} is the asset this method uses")
        model = loader.from_pretrained(TIMESFM_2_5)
        return model
    except Unavailable:
        raise
    except Exception as why:
        raise Unavailable(f"{TIMESFM_2_5} could not be loaded here: {type(why).__name__}: {why}")


class TimesFM25(ForecastModelAdapter):
    method_key = "timesfm_2_5"
    version = "2.5-200m"

    def _run(self, context: ForecastContext) -> ForecastOutput:
        series = context.history
        if len(series.points) < MIN_CONTEXT[series.frequency]:
            raise Ineligible(f"needs {MIN_CONTEXT[series.frequency]} periods of context, has {len(series.points)}")
        model = _model()
        steps = max(steps_for(weeks, series.frequency, series.offset_days)[0] for weeks in context.horizons)
        values = list(series.values)
        try:
            model.compile(getattr(__import__("timesfm"), "ForecastConfig")(max_context=max(64, len(values)),
                                                                            max_horizon=steps))
        except Exception:
            pass                                             # older builds need no compile step
        point, quantile = model.forecast(horizon=steps, inputs=[values])
        central = [float(v) for v in point[0][:steps]]
        # The 2.5 API returns quantiles at 0.1 .. 0.9 after the mean in position 0.
        paths = {}
        try:
            levels = [round(0.1 * k, 1) for k in range(1, 10)]
            for q in DEFAULT_QUANTILES:
                index = levels.index(q) + 1
                paths[q] = [float(v) for v in quantile[0][:steps, index]]
        except Exception:
            paths = {}
        return self.succeeded(context, points_from_path(context, central, paths),
                              {"asset": TIMESFM_2_5, "context_periods": len(values), "steps": steps})


def timesfm_adapters() -> list[ForecastModelAdapter]:
    return [TimesFM25()]
