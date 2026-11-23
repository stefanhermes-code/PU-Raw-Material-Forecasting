from datetime import timedelta
import pandas as pd

#: The application's one mark for "we do not know this".
#:
#: Streamlit prints the word **None** for a null in any column it can - measured on 31 October 2026,
#: Streamlit 1.63.0 with pandas 3.0.5: a `float64` holding `NaN`, a nullable `Int64` holding `pd.NA`
#: and a `string` holding `pd.NA` all render "None", with or without a `column_config`. Only a text
#: column renders this dash. A reader cannot tell "None" from a fault in the page, so any column that
#: can carry an unknown is built as text with this in it.
#:
#: Here, and imported, so the two tables that were each deciding it separately cannot drift apart.
#: Around twenty other sites still write this dash as their own literal; one mark spelled out in
#: twenty-two places is a sweep of its own and is listed in the masterplan.
UNKNOWN = "—"


def status_message(status: str, reasons=()) -> str:
    status = str(status).upper()
    if reasons:
        return f"{status}: " + "; ".join(str(r) for r in reasons)
    if status == "CONTROLLED_TEST":
        return "CONTROLLED_TEST: data gate passed; forecast remains under validation."
    if status == "OPERATIONAL":
        return "OPERATIONAL: data and backtest gates passed for the current series."
    return f"{status}: forecast is not cleared for operational use."


def build_forecast_frame(last_date, naive: pd.Series, ets: pd.Series) -> pd.DataFrame:
    horizon = min(len(naive), len(ets))
    last = pd.Timestamp(last_date)
    dates = [last + timedelta(days=7 * (i + 1)) for i in range(horizon)]
    return pd.DataFrame({
        "date": dates,
        "Naive": naive.iloc[:horizon].astype(float).to_list(),
        "ETS": ets.iloc[:horizon].astype(float).to_list(),
    })


def operational_gate(improvement_vs_naive_pct: float, directional_accuracy: float, min_improvement_pct: float = 5.0, min_directional_accuracy: float = 0.55) -> bool:
    return improvement_vs_naive_pct >= min_improvement_pct and directional_accuracy >= min_directional_accuracy


def build_user_forecast_frame(last_date, forecast: pd.Series) -> pd.DataFrame:
    last = pd.Timestamp(last_date)
    dates = [last + timedelta(days=7 * (i + 1)) for i in range(len(forecast))]
    return pd.DataFrame({
        "date": dates,
        "Forecast": forecast.astype(float).to_list(),
    })
