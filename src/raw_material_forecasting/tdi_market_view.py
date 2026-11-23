from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from raw_material_forecasting import units
from raw_material_forecasting.fx import source_currency


@dataclass(frozen=True)
class TDIChinaDomesticState:
    evidence: list[dict[str, Any]]
    reference_value: float | None
    reference_date: str | None
    reference_identity_key: str | None
    reference_sources: tuple[str, ...]
    reference_source_count: int
    evidence_sources: tuple[str, ...]
    evidence_source_count: int
    latest_observation_date: str | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    unit: str | None


def _date_only(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def _date_sort_key(value: Any) -> datetime:
    if value is None:
        return datetime.min
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)[:10])
        except ValueError:
            return datetime.min


def _is_china_tdi_evidence(obs: dict[str, Any]) -> bool:
    product = str(obs.get("product_family") or "").upper()
    geography = str(obs.get("market_geography") or "")
    basis = str(obs.get("commercial_basis") or "").upper()
    currency = source_currency(obs)
    unit = str(obs.get("unit") or "").lower()
    return (
        product == "TDI"
        and "CHINA" in geography.upper()
        and basis == "EXW"
        and currency in {"CNY", "RMB"}
        and units.is_tonne(unit)
    )


def _is_national_china_reference(ref: dict[str, Any]) -> bool:
    return (
        str(ref.get("product_family") or "").upper() == "TDI"
        and str(ref.get("market_geography") or "").strip().lower() == "china"
        and str(ref.get("commercial_basis") or "").upper() == "EXW"
        and source_currency(ref) in {"CNY", "RMB"}
        and units.is_tonne(ref.get("unit"))
    )



def _comparable_china_consensus(evidence: list[dict[str, Any]]):
    eligible = [
        obs for obs in evidence
        if str(obs.get("market_geography") or "").strip().lower() in {"china", "east china"}
        and str(obs.get("commercial_basis") or "").upper() == "EXW"
        and source_currency(obs) in {"CNY", "RMB"}
        and units.is_tonne(obs.get("unit"))
        and obs.get("price") is not None
        and _date_sort_key(obs.get("observation_date")) != datetime.min
    ]
    if not eligible:
        return None

    latest = max(_date_sort_key(obs.get("observation_date")) for obs in eligible)
    latest_day = latest.date()
    same_day = [obs for obs in eligible if _date_sort_key(obs.get("observation_date")).date() == latest_day]

    latest_by_source: dict[str, dict[str, Any]] = {}
    for obs in same_day:
        source = str(obs.get("source_id") or "")
        if not source:
            continue
        current = latest_by_source.get(source)
        if current is None:
            latest_by_source[source] = obs
            continue
        current_key = (_date_sort_key(current.get("available_from") or current.get("publication_date") or current.get("observation_date")), str(current.get("observation_id") or ""))
        candidate_key = (_date_sort_key(obs.get("available_from") or obs.get("publication_date") or obs.get("observation_date")), str(obs.get("observation_id") or ""))
        if candidate_key > current_key:
            latest_by_source[source] = obs

    rows = [latest_by_source[source] for source in sorted(latest_by_source)]
    if not rows:
        return None
    return {
        "value": sum(float(row["price"]) for row in rows) / len(rows),
        "date": latest_day.isoformat(),
        "sources": tuple(sorted(latest_by_source)),
        "rows": rows,
    }

def build_tdi_china_domestic_state(snapshot) -> TDIChinaDomesticState:
    observations = [dict(obs) for obs in getattr(snapshot, "observations", [])]
    evidence = [obs for obs in observations if _is_china_tdi_evidence(obs)]
    evidence.sort(key=lambda row: (_date_sort_key(row.get("observation_date")), str(row.get("source_id") or "")))

    by_id = {str(obs.get("observation_id")): obs for obs in observations if obs.get("observation_id") is not None}
    candidates: list[tuple[int, int, datetime, dict[str, Any], tuple[str, ...]]] = []
    for ref in getattr(snapshot, "references", []):
        if not _is_national_china_reference(ref):
            continue
        sources = tuple(
            sorted(
                {
                    str(by_id[str(obs_id)].get("source_id"))
                    for obs_id in (ref.get("observation_ids") or [])
                    if str(obs_id) in by_id and by_id[str(obs_id)].get("source_id")
                }
            )
        )
        if not sources:
            continue
        grade = str(ref.get("product_grade_or_chemistry") or "").upper().replace(" ", "")
        t80_preference = 1 if grade in {"T-80", "T80", "80/20", "TDI80/20"} else 0
        candidates.append((t80_preference, len(sources), _date_sort_key(ref.get("reference_date")), dict(ref), sources))

    selected_ref: dict[str, Any] | None = None
    selected_sources: tuple[str, ...] = ()
    if candidates:
        _pref, _count, _date, selected_ref, selected_sources = max(candidates, key=lambda item: item[:3])

    comparable_consensus = None if selected_ref is not None else _comparable_china_consensus(evidence)
    evidence_sources = tuple(sorted({str(obs.get("source_id")) for obs in evidence if obs.get("source_id")}))
    prices = [float(obs["price"]) for obs in evidence if obs.get("price") is not None]
    latest = max((_date_sort_key(obs.get("observation_date")) for obs in evidence), default=None)
    latest_date = latest.date().isoformat() if latest is not None else None

    if selected_ref is not None:
        identity_key = selected_ref.get("identity_key")
        identity_evidence = [obs for obs in evidence if obs.get("identity_key") == identity_key]
        identity_prices = [float(obs["price"]) for obs in identity_evidence if obs.get("price") is not None]
        currency = str(selected_ref.get("currency") or "CNY")
        unit = units.canonical_unit(selected_ref.get("unit") or units.MT)
        price_min = min(identity_prices) if identity_prices else (min(prices) if prices else None)
        price_max = max(identity_prices) if identity_prices else (max(prices) if prices else None)
        return TDIChinaDomesticState(
            evidence=evidence,
            reference_value=float(selected_ref["reference_value"]),
            reference_date=_date_only(selected_ref.get("reference_date")),
            reference_identity_key=str(identity_key) if identity_key else None,
            reference_sources=selected_sources,
            reference_source_count=len(selected_sources),
            evidence_sources=evidence_sources,
            evidence_source_count=len(evidence_sources),
            latest_observation_date=latest_date,
            price_min=price_min,
            price_max=price_max,
            currency=currency,
            unit=unit,
        )

    if comparable_consensus is not None:
        rows = comparable_consensus["rows"]
        return TDIChinaDomesticState(
            evidence=evidence,
            reference_value=float(comparable_consensus["value"]),
            reference_date=str(comparable_consensus["date"]),
            reference_identity_key=f"TDI|COMPARABLE_CHINA_EXW|CNY|{units.MT}",
            reference_sources=comparable_consensus["sources"],
            reference_source_count=len(comparable_consensus["sources"]),
            evidence_sources=evidence_sources,
            evidence_source_count=len(evidence_sources),
            latest_observation_date=latest_date,
            price_min=min(float(row["price"]) for row in rows),
            price_max=max(float(row["price"]) for row in rows),
            currency=str(rows[0].get("currency") or "CNY"),
            unit=units.MT,
        )

    currency = str(evidence[-1].get("currency") or "CNY") if evidence else None
    unit = units.canonical_unit(evidence[-1].get("unit") or units.MT) if evidence else None
    return TDIChinaDomesticState(
        evidence=evidence,
        reference_value=None,
        reference_date=None,
        reference_identity_key=None,
        reference_sources=(),
        reference_source_count=0,
        evidence_sources=evidence_sources,
        evidence_source_count=len(evidence_sources),
        latest_observation_date=latest_date,
        price_min=min(prices) if prices else None,
        price_max=max(prices) if prices else None,
        currency=currency,
        unit=unit,
    )
