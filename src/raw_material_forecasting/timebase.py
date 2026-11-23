import pandas as pd


def _dated_prices(df: pd.DataFrame) -> pd.Series:
    data = df[["observation_date", "price_per_tonne"]].copy()
    data["observation_date"] = pd.to_datetime(data["observation_date"])
    data = data.dropna().sort_values("observation_date")
    data = data.drop_duplicates(subset=["observation_date"], keep="last")
    return data.set_index("observation_date")["price_per_tonne"].astype(float)


def has_continuous_weekly_observations(df: pd.DataFrame) -> bool:
    series = _dated_prices(df)
    if len(series) < 2:
        return False
    gaps = series.index.to_series().diff().dropna().dt.days
    return bool((gaps == 7).all())


def normalize_weekly_price_series(df: pd.DataFrame) -> pd.Series:
    series = _dated_prices(df)
    if series.empty:
        return series
    weekly_index = pd.date_range(series.index.min(), series.index.max(), freq="7D")
    expanded_index = series.index.union(weekly_index).sort_values()
    interpolated = series.reindex(expanded_index).interpolate(method="time")
    return interpolated.reindex(weekly_index)
