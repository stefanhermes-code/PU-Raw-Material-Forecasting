"""Governed adapters: every forecast method behind one contract (Adaptive Forecast Method Selection, WP2).

The engine's own models keep their `ForecastModel` protocol (`app.forecasting.adapter`); an adapter
wraps one of them, or a foundation model, or a blend, and answers a `ForecastContext` with a
`ForecastOutput` that always carries an execution status. Nothing in here raises past the adapter,
and nothing in here substitutes one method for another: a failure is reported as a failure.
"""
from app.forecasting.adapters.base import (
    ExecutionStatus, ForecastContext, ForecastModelAdapter, ForecastOutput,
)

__all__ = ["ExecutionStatus", "ForecastContext", "ForecastModelAdapter", "ForecastOutput"]
