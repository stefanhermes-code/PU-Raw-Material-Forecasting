from dataclasses import dataclass
from datetime import date
import pandas as pd

from .contracts import SourceRights


IDENTITY_COLUMNS = [
    "product_family", "grade", "origin", "destination",
    "price_basis", "packaging", "currency",
]


@dataclass(frozen=True)
class SeriesAuditResult:
    observation_count: int
    start_date: date | None
    end_date: date | None
    largest_gap_days: int | None
    freshness_days: int | None
    release_status: str
    reasons: tuple[str, ...]
    operational_rights_clear: bool


def audit_series(df: pd.DataFrame, as_of: date, min_observations: int = 24, max_freshness_days: int = 35) -> SeriesAuditResult:
    if df.empty:
        return SeriesAuditResult(0, None, None, None, None, "RESEARCH", ("no observations",), False)

    data = df.copy()
    data["observation_date"] = pd.to_datetime(data["observation_date"]).dt.date
    reasons: list[str] = []

    if any(col not in data.columns for col in IDENTITY_COLUMNS):
        missing = [col for col in IDENTITY_COLUMNS if col not in data.columns]
        reasons.append("missing identity columns: " + ", ".join(missing))
    else:
        unique_identities = data[IDENTITY_COLUMNS].astype(str).drop_duplicates()
        if len(unique_identities) != 1:
            reasons.append("mixed canonical identities")

    dates = sorted(data["observation_date"].dropna().tolist())
    count = len(dates)
    start_date = dates[0] if dates else None
    end_date = dates[-1] if dates else None
    largest_gap = max(((b - a).days for a, b in zip(dates, dates[1:])), default=0) if dates else None
    freshness = (as_of - end_date).days if end_date else None

    if count < min_observations:
        reasons.append(f"fewer than {min_observations} observations")
    if freshness is not None and freshness > max_freshness_days:
        reasons.append(f"stale series: {freshness} days since last observation")
    if "publication_date_verified" in data.columns and not data["publication_date_verified"].fillna(False).astype(bool).all():
        reasons.append("publication dates unverified")

    operational_rights_clear = False
    if "rights_classification" in data.columns:
        operational_rights_clear = all(
            SourceRights(value).eligible_for_operational
            for value in data["rights_classification"].astype(str)
        )

    status = "CONTROLLED_TEST" if not reasons else "RESEARCH"
    return SeriesAuditResult(
        observation_count=count,
        start_date=start_date,
        end_date=end_date,
        largest_gap_days=largest_gap,
        freshness_days=freshness,
        release_status=status,
        reasons=tuple(reasons),
        operational_rights_clear=operational_rights_clear,
    )


@dataclass(frozen=True)
class HorizonGateResult:
    status: str
    independent_episodes: int
    reasons: tuple[str, ...]


def evaluate_horizon_gate(
    observation_count: int,
    context_observations: int,
    horizon_observations: int,
    regime_count: int,
    publication_lag_verified: bool,
    significance_passed: bool | None,
    operational_rights_clear: bool,
    target_frequency_continuous: bool = True,
    min_observations: int = 60,
    min_independent_episodes: int = 50,
    min_regimes: int = 3,
) -> HorizonGateResult:
    if horizon_observations <= 0:
        raise ValueError("horizon_observations must be positive")
    independent_episodes = max((observation_count - context_observations) // horizon_observations, 0)
    research_reasons: list[str] = []
    controlled_reasons: list[str] = []

    if observation_count < min_observations:
        research_reasons.append(f"fewer than {min_observations} observations")
    if not target_frequency_continuous:
        research_reasons.append("target-frequency continuity unverified")
    if not publication_lag_verified:
        research_reasons.append("publication lag unverified")
    if not operational_rights_clear:
        research_reasons.append("operational source rights not cleared")
    if regime_count < min_regimes:
        research_reasons.append(f"fewer than {min_regimes} distinct regimes")

    if independent_episodes < min_independent_episodes:
        controlled_reasons.append(f"fewer than {min_independent_episodes} independent episodes")
    if significance_passed is not True:
        controlled_reasons.append("significance test not passed")

    if research_reasons:
        return HorizonGateResult("RESEARCH", independent_episodes, tuple(research_reasons + controlled_reasons))
    if controlled_reasons:
        return HorizonGateResult("CONTROLLED_TEST", independent_episodes, tuple(controlled_reasons))
    return HorizonGateResult("OPERATIONAL", independent_episodes, ())
