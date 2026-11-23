"""Method Fit, as a reader and an administrator see it (Adaptive Forecast Method Selection, WP5, Task 11).

Pure functions from the engine's `/model-fit` answer to the rows the pages draw, so what is shown can
be tested without a browser:

- `user_rows` - the compact display under the forecast: per horizon, the method in force, whether it
  was chosen automatically or by an administrator, the last review's date, that method's matched MAE
  and directional accuracy, and when the next review is due.
- `admin_rows` - the management page: per horizon, the automatic winner beside the active method, the
  override (who, why) with an OVERRIDE RECOMMENDATION when the two differ, the top alternatives with
  Challengers marked, and the selection history - enough to trace why a method is active without
  reading logs or tables (plan, Task 11, step 4).
"""
from __future__ import annotations

from .ui_helpers import UNKNOWN

from typing import Any

METHOD_NAMES = {
    "momentum": "Momentum", "weighted_error": "Weighted error (Europe)", "ets": "Exponential smoothing",
    "naive_last": "No change", "seasonal_naive": "Same as last year",
    "chronos2_univariate": "Chronos-2", "chronos2_recent_36m": "Chronos-2 (recent 36 months)",
    "chronos2_log_price": "Chronos-2 (log price)", "chronos_bolt_tiny": "Chronos-Bolt tiny",
    "timesfm_2_5": "TimesFM 2.5", "foundation_blend_equal": "Foundation blend (equal)",
    "foundation_blend_adaptive": "Foundation blend (adaptive)", "lightgbm_related": "LightGBM (related markets)",
    "sarimax_related": "SARIMAX (related markets)", "chronos2_related_market": "Chronos-2 (related market)",
    "chronos2_joint_network": "Chronos-2 (market network)", "intelligence_enhanced": "Intelligence-enhanced",
    "feedstock_regression": "Feedstock regression",
}
SOURCE_NAMES = {"AUTOMATIC": "Automatic", "MANUAL_OVERRIDE": "Administrator override",
                "NOT_GOVERNED": "Not yet governed"}


def method_name(key: str | None) -> str:
    if not key:
        return UNKNOWN
    return METHOD_NAMES.get(key, key.replace("_", " "))


def _day(text: str | None) -> str:
    return str(text)[:10] if text else UNKNOWN


def _number(value, digits: int = 0) -> str:
    if value is None:
        return UNKNOWN
    return f"{float(value):,.{digits}f}"


def _share(value) -> str:
    return UNKNOWN if value is None else f"{float(value):.0%}"


def horizon_label(weeks: str | int) -> str:
    return f"{int(weeks)} weeks"


def any_governed(fit: dict[str, Any]) -> bool:
    return any((h or {}).get("selection") not in (None, "NOT_GOVERNED") for h in (fit.get("horizons") or {}).values())


def user_rows(fit: dict[str, Any]) -> list[dict[str, str]]:
    last = (fit.get("last_review") or {}).get("finished_at")
    rows = []
    for weeks, h in sorted((fit.get("horizons") or {}).items(), key=lambda kv: int(kv[0])):
        rows.append({
            "Horizon": horizon_label(weeks),
            "Method in force": method_name(h.get("active_method")),
            "Chosen": SOURCE_NAMES.get(h.get("selection") or "NOT_GOVERNED", h.get("selection") or UNKNOWN),
            "Matched MAE": _number(h.get("matched_mae")),
            "Direction right": _share(h.get("directional_accuracy")),
            "Last review": _day(last),
            # "Next review" stood here and was a dash on every row from 29 October 2026, when the
            # quarterly cadence went. There is no next review to name: a method is re-checked when
            # a reading arrives that its choice has not seen.
        })
    return rows


def admin_rows(fit: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for weeks, h in sorted((fit.get("horizons") or {}).items(), key=lambda kv: int(kv[0])):
        override = h.get("override") or {}
        out.append({
            "horizon_weeks": int(weeks),
            "Horizon": horizon_label(weeks),
            "Active method": method_name(h.get("active_method")),
            "Chosen": SOURCE_NAMES.get(h.get("selection") or "NOT_GOVERNED", h.get("selection") or UNKNOWN),
            "Automatic winner": method_name(h.get("automatic_winner")),
            "Override": (f"{method_name(override.get('method_key'))} — {override.get('actor')}: {override.get('reason')}"
                         if override else UNKNOWN),
            "Override recommendation": (
                f"The review ranks {method_name(h.get('automatic_winner'))} best; the override keeps "
                f"{method_name(override.get('method_key'))}. Review the override."
                if h.get("override_recommendation") else ""),
            # "Rank" and "Dates" were raw while "Matched MAE" and "Direction right" beside them
            # went through helpers that return the mark: the same rule, applied to two of four
            # columns in one dict. A null in either renders as the word "None" on screen
            # (measured, Streamlit 1.63.0 / pandas 3.0.5), which reads as a fault in the page.
            "alternatives": [{"Rank": _number(a.get("rank")),
                              "Method": method_name(a.get("method_key")),
                              "Matched MAE": _number(a.get("matched_mae")),
                              "Direction right": _share(a.get("directional_accuracy")),
                              "Dates": _number(a.get("matched_dates")),
                              "Status at review": "Challenger" if a.get("challenger") else
                              (a.get("status_at_review") or "").replace("_", " ").title()}
                             for a in (h.get("top_alternatives") or [])],
            "challengers_ahead": [method_name(a.get("method_key")) for a in (h.get("challengers_ahead") or [])],
            "history": [{"Since": _day(x.get("selected_at")), "Method": method_name(x.get("method_key")),
                         "Chosen": SOURCE_NAMES.get(x.get("source"), x.get("source")),
                         "In force": "yes" if x.get("active") else "no"} for x in (h.get("history") or [])],
        })
    return out


def why_line(fit: dict[str, Any], weeks: int) -> str:
    """One sentence: why the method in force at this horizon is in force."""
    h = (fit.get("horizons") or {}).get(str(weeks)) or {}
    source = h.get("selection") or "NOT_GOVERNED"
    if source == "NOT_GOVERNED":
        state = "switched off" if not fit.get("automatic_selection_enabled") else "not yet made for this market"
        return f"No governed method yet: automatic selection is {state}."
    if source == "MANUAL_OVERRIDE":
        o = h.get("override") or {}
        return (f"{method_name(h.get('active_method'))} by administrator override ({o.get('actor')}, "
                f"{_day(o.get('since'))}): {o.get('reason')}.")
    last = fit.get("last_review") or {}
    return (f"{method_name(h.get('active_method'))} ranked best of the Production Eligible methods in the "
            f"Model Fit Review of {_day(last.get('finished_at'))} (matched MAE {_number(h.get('matched_mae'))}, "
            f"direction right {_share(h.get('directional_accuracy'))}).")
