from dataclasses import dataclass
from datetime import date
import numpy as np
import pandas as pd

from .forecasting import naive_forecast, ets_forecast


@dataclass(frozen=True)
class BacktestResult:
    model_name: str
    horizon_weeks: int
    forecast_count: int
    mae: float
    naive_mae: float
    improvement_vs_naive_pct: float
    directional_accuracy: float


def available_history(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    data = df.copy()
    data["publication_date"] = pd.to_datetime(data["publication_date"]).dt.date
    data["observation_date"] = pd.to_datetime(data["observation_date"]).dt.date
    return data.loc[data["publication_date"] <= cutoff].sort_values("observation_date").reset_index(drop=True)


def _forecast(model_name: str, series: pd.Series, horizon: int) -> pd.Series:
    if model_name == "naive":
        return naive_forecast(series, horizon)
    if model_name == "ets":
        return ets_forecast(series, horizon)
    raise ValueError(f"unknown model: {model_name}")


def rolling_backtest(df: pd.DataFrame, horizon_weeks: int, model_name: str = "ets", min_train: int = 16) -> BacktestResult:
    if horizon_weeks <= 0:
        raise ValueError("horizon_weeks must be positive")
    data = df.copy()
    data["observation_date"] = pd.to_datetime(data["observation_date"]).dt.date
    data["publication_date"] = pd.to_datetime(data["publication_date"]).dt.date
    data = data.sort_values("observation_date").reset_index(drop=True)
    if len(data) < min_train + horizon_weeks:
        raise ValueError("insufficient history for requested backtest")

    model_errors = []
    naive_errors = []
    direction_hits = []
    forecast_count = 0

    for origin_idx in range(min_train - 1, len(data) - horizon_weeks):
        origin_date = data.loc[origin_idx, "observation_date"]
        training = available_history(data.iloc[: origin_idx + 1], origin_date)
        if len(training) < min_train:
            continue
        future = data.iloc[origin_idx + 1 : origin_idx + 1 + horizon_weeks]
        actual = future["price_per_tonne"].astype(float).reset_index(drop=True)
        if len(actual) != horizon_weeks:
            continue

        train_series = training["price_per_tonne"].astype(float)
        model_fc = _forecast(model_name, train_series, horizon_weeks)
        naive_fc = naive_forecast(train_series, horizon_weeks)

        model_errors.extend(np.abs(actual.to_numpy() - model_fc.to_numpy()).tolist())
        naive_errors.extend(np.abs(actual.to_numpy() - naive_fc.to_numpy()).tolist())

        actual_direction = np.sign(float(actual.iloc[-1]) - float(train_series.iloc[-1]))
        model_direction = np.sign(float(model_fc.iloc[-1]) - float(train_series.iloc[-1]))
        direction_hits.append(1.0 if actual_direction == model_direction else 0.0)
        forecast_count += 1

    if forecast_count == 0:
        raise ValueError("no valid rolling forecast origins")

    mae = float(np.mean(model_errors))
    naive_mae = float(np.mean(naive_errors))
    improvement = 0.0 if naive_mae == 0 else float((naive_mae - mae) / naive_mae * 100.0)
    directional_accuracy = float(np.mean(direction_hits)) if direction_hits else 0.0
    return BacktestResult(
        model_name=model_name,
        horizon_weeks=horizon_weeks,
        forecast_count=forecast_count,
        mae=mae,
        naive_mae=naive_mae,
        improvement_vs_naive_pct=improvement,
        directional_accuracy=directional_accuracy,
    )
