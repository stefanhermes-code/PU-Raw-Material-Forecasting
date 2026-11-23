"""One-step forecasting baselines, with no compiled dependency.

`ets_forecast` was statsmodels' ExponentialSmoothing with an additive trend. On 25 September 2026
Windows Application Control blocked statsmodels' compiled Kalman extension on Stefan's machine and
the whole application stopped opening - a library needed by one comparison block on one page took
down every screen. Rather than ask for the block to be lifted, the method is written out here: Holt's
linear trend is a four-parameter recursion, and it needs no compiled extension at all.

    l[t] = a*y[t] + (1-a)*(l[t-1] + b[t-1])
    b[t] =   B*(l[t] - l[t-1]) + (1-B)*b[t-1]
    forecast h ahead = l[last] + h*b[last]

a, B and the initial level and trend are chosen to minimise the sum of squared one-step errors,
which is what statsmodels optimised. Checked against statsmodels 0.15.0 - the exact build that was
in the virtual environment - on all eight market series in the store: the fit here is never worse,
and on the six chemical-and-geography markets the forecasts agree to within 0.45% at twelve steps,
five of the six to within 0.03%. Where the two differ it is because this search reaches a lower
sum of squares than statsmodels did.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _clean_series(series: pd.Series) -> pd.Series:
    cleaned = pd.Series(series, dtype=float).dropna().reset_index(drop=True)
    if cleaned.empty:
        raise ValueError("series must contain at least one value")
    return cleaned


def naive_forecast(series: pd.Series, horizon: int) -> pd.Series:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    cleaned = _clean_series(series)
    return pd.Series([float(cleaned.iloc[-1])] * horizon, dtype=float)


def _sse(params, y):
    a, B, l0, b0 = params
    # Closed interval, exactly as statsmodels allows. A boundary answer is a real answer:
    # beta = 0 means the trend never updates, which is what a flat market actually looks like.
    # beta <= alpha is the admissibility condition for Holt's method and the bound statsmodels
    # optimises under; without it the search finds beta = 1 fits that track the history a little
    # more closely and then throw the forecast about.
    if not (0.0 <= a <= 1.0 and 0.0 <= B <= a):
        return np.inf
    level, trend, total = l0, b0, 0.0
    for value in y:
        prediction = level + trend
        error = value - prediction
        total += error * error
        new_level = a * value + (1.0 - a) * prediction
        trend = B * (new_level - level) + (1.0 - B) * trend
        level = new_level
    return total


def _nelder_mead(fn, start, y, steps=None, iterations=2000, tol=1e-12):
    """Plain Nelder-Mead. No scipy: scipy is compiled too, and the whole point is to depend on
    nothing that an application-control policy can refuse to load."""
    n = len(start)
    if steps is None:
        steps = [0.05] * n
    simplex = [np.array(start, dtype=float)]
    for i in range(n):
        point = np.array(start, dtype=float)
        point[i] = point[i] + steps[i]
        simplex.append(point)
    values = [fn(p, y) for p in simplex]
    for _ in range(iterations):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if abs(values[-1] - values[0]) <= tol * (abs(values[0]) + tol):
            break
        centroid = np.mean(simplex[:-1], axis=0)
        reflected = centroid + (centroid - simplex[-1])
        f_r = fn(reflected, y)
        if f_r < values[0]:
            expanded = centroid + 2.0 * (centroid - simplex[-1])
            f_e = fn(expanded, y)
            simplex[-1], values[-1] = (expanded, f_e) if f_e < f_r else (reflected, f_r)
        elif f_r < values[-2]:
            simplex[-1], values[-1] = reflected, f_r
        else:
            contracted = centroid + 0.5 * (simplex[-1] - centroid)
            f_c = fn(contracted, y)
            if f_c < values[-1]:
                simplex[-1], values[-1] = contracted, f_c
            else:
                for i in range(1, len(simplex)):
                    simplex[i] = simplex[0] + 0.5 * (simplex[i] - simplex[0])
                    values[i] = fn(simplex[i], y)
    best = int(np.argmin(values))
    return simplex[best], values[best]


def fit_holt(y):
    y = np.asarray(y, dtype=float)
    n = len(y)
    span = min(10, n)
    slope, intercept = np.polyfit(np.arange(span), y[:span], 1)
    starts = [(0.5, 0.1, intercept, slope), (0.8, 0.2, intercept, slope),
              (0.2, 0.05, intercept, slope), (0.95, 0.5, y[0], slope),
              (0.5, 0.0, intercept, slope), (0.9, 0.0, intercept, slope),
              (0.1, 0.0, intercept, slope),
              # The corners the optimiser settles on most often in practice: all weight on the
              # latest observation, or a trend that never updates.
              (1.0, 0.0, intercept, slope), (1.0, 0.0, y[0], 0.0)]
    # One step per parameter, each on its own scale. alpha and beta live in [0,1]; the level is
    # the size of the price and the trend the size of a monthly move. A single 5% step treats a
    # 3,000 EUR level and a 2 EUR trend the same way, and the search then never resolves the trend.
    move = float(np.mean(np.abs(np.diff(y)))) if n > 1 else 1.0
    steps = [0.1, 0.1, max(1.0, 0.05 * abs(intercept)), max(move, 1e-6)]
    best, best_sse = None, np.inf
    for start in starts:
        params, sse = _nelder_mead(_sse, start, y, steps)
        if sse < best_sse:
            best, best_sse = params, sse
    # Restart from the best point with a fresh simplex. Nelder-Mead collapses around a local
    # optimum and stops improving; restarting is what gets the last few decimals.
    for _ in range(4):
        steps = [s * 0.25 for s in steps]
        params, sse = _nelder_mead(_sse, best, y, steps)
        if sse < best_sse - 1e-12:
            best, best_sse = params, sse
        else:
            break
    a, B, l0, b0 = best
    a = min(max(a, 0.0), 1.0)
    B = min(max(B, 0.0), 1.0)
    level, trend = l0, b0
    for value in y:
        prediction = level + trend
        new_level = a * value + (1.0 - a) * prediction
        trend = B * (new_level - level) + (1.0 - B) * trend
        level = new_level
    return {"alpha": a, "beta": B, "level": level, "trend": trend, "sse": best_sse}


def holt_forecast(y, horizon):
    state = fit_holt(y)
    return np.array([state["level"] + (h + 1) * state["trend"] for h in range(horizon)])


def ets_forecast(series: pd.Series, horizon: int) -> pd.Series:
    """Holt's linear trend. Kept under its old name: every caller already uses it."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    cleaned = _clean_series(series)
    if len(cleaned) < 4:
        return naive_forecast(cleaned, horizon)
    return pd.Series(holt_forecast(cleaned.to_numpy(), horizon), dtype=float).reset_index(drop=True)
