from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.classification.classifier import classify_record
from app.domain.enums import EvidenceClass, RunState
from app.identity.resolver import resolve_identity
from app.ingestion.runner import IngestionRunResult, IngestionRunner
from app.quality.scoring import evidence_weight
from app.scheduling.scheduler import due_source_ids as scheduled_due_source_ids
from app.reference.weighting import weighted_median
from app.sources.registry import SourceRegistry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any, fallback: datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
    return fallback.date()


class CloudMarketDataService:
    """Run existing collectors while persisting structured state to Supabase."""

    def __init__(self, *, market_store, raw_store, registry: SourceRegistry | None = None):
        self.market_store = market_store
        self.registry = registry or SourceRegistry.default()
        self.runner = IngestionRunner(self.registry, raw_store=raw_store)

    def due_source_ids(self, now: datetime | None = None) -> list[str]:
        last_checked: dict[str, datetime | None] = {}
        for row in self.market_store.list_source_state():
            value = row.get("last_checked_at")
            if isinstance(value, str) and value:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            last_checked[str(row["source_id"])] = value
        return scheduled_due_source_ids(self.registry, last_checked=last_checked, now=now)

    def rebuild_reference(self, identity_id: str) -> None:
        if not hasattr(self.market_store, "list_price_observations") or not hasattr(self.market_store, "add_market_reference"):
            return
        rows = list(self.market_store.list_price_observations(identity_id=identity_id))
        usable = [row for row in rows if row.get("native_price") is not None and row.get("observation_date") and row.get("source_id")]
        if not usable:
            return

        def row_date(row):
            value = row["observation_date"]
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value)[:10])

        latest_date = max(row_date(row) for row in usable)
        window_start = latest_date - timedelta(days=3)
        window = [row for row in usable if window_start <= row_date(row) <= latest_date]
        latest_by_source: dict[str, dict] = {}
        for row in window:
            source_id = str(row["source_id"])
            current = latest_by_source.get(source_id)
            if current is None or (row_date(row), str(row.get("created_at") or "")) > (row_date(current), str(current.get("created_at") or "")):
                latest_by_source[source_id] = row
        contributors = sorted(latest_by_source.values(), key=lambda row: (row_date(row), str(row.get("created_at") or "")), reverse=True)
        if len(contributors) < 2:
            return
        values = [(float(row["native_price"]), float(row.get("confidence") or 1.0)) for row in contributors]
        reference_value = weighted_median(values)
        prices = [value for value, _ in values]
        currency = str(contributors[0].get("currency") or "UNKNOWN")
        unit = str(contributors[0].get("unit") or "t")
        self.market_store.add_market_reference(
            identity_id=identity_id,
            reference_date=latest_date,
            reference_value=reference_value,
            currency=currency,
            unit=unit,
            source_ids=sorted(latest_by_source),
            observation_ids=[str(row["observation_id"]) for row in contributors],
            method="WEIGHTED_MEDIAN_LATEST_PER_SOURCE_3D",
            dispersion=max(prices) - min(prices),
            confidence=min(1.0, sum(float(row.get("confidence") or 0.0) for row in contributors) / len(contributors)),
            normalization_scope={"window_days": 3, "latest_per_source": True, "normalized": False},
        )

    def collect(self, connector) -> IngestionRunResult:
        source = self.registry.get(connector.source_id)
        run_id = self.market_store.begin_run(connector.source_id)
        checked_at = _now()
        result = self.runner.run(connector)

        evidence_by_url: dict[str, str] = {}
        evidence_change_flags: list[bool] = []
        for source_url, raw_ref in result.raw_by_url.items():
            publication_date = next(
                (record.publication_date for record in result.records if record.source_url == source_url and record.publication_date),
                None,
            )
            stored = self.market_store.upsert_raw_evidence(
                source_id=connector.source_id,
                run_id=run_id,
                collected_at=checked_at,
                publication_date=publication_date,
                source_url=source_url,
                content_hash=raw_ref.content_hash,
                mime_type="text/html",
                storage_path=raw_ref.path,
                extraction_method=source.collection_method,
                rights_class=source.rights_class,
                metadata={"run_state": result.state.value},
            )
            if isinstance(stored, tuple):
                evidence_id, is_new = stored
            else:
                evidence_id, is_new = stored, True
            evidence_by_url[source_url] = evidence_id
            evidence_change_flags.append(bool(is_new))

        qualified = 0
        impacted_identity_ids: set[str] = set()
        for record in result.records:
            if classify_record(record.data) != EvidenceClass.DIRECT_MARKET_PRICE:
                continue
            evidence_id = evidence_by_url.get(record.source_url)
            if evidence_id is None:
                continue
            identity = resolve_identity(record.data)
            identity_id = self.market_store.ensure_identity(
                {
                    "identity_key": identity.identity_key,
                    "product_family": identity.product_family,
                    "product_grade_or_chemistry": identity.product_grade_or_chemistry,
                    "origin_geography": identity.origin,
                    "market_geography": identity.market_geography,
                    "destination_geography": identity.destination,
                    "commercial_basis": identity.incoterm_or_commercial_basis,
                    "incoterm": record.data.get("incoterm"),
                    "packaging": identity.packaging,
                    "currency": identity.currency,
                    "unit": identity.unit,
                    "observation_type": identity.observation_type,
                    "comparability_group": identity.comparability_group,
                }
            )
            complete = all(
                [
                    identity.product_grade_or_chemistry,
                    identity.incoterm_or_commercial_basis,
                    identity.packaging,
                ]
            )
            observation_fallback = record.publication_date or record.collected_at
            price = float(record.data["price"])
            self.market_store.add_price_observation(
                source_id=connector.source_id,
                run_id=run_id,
                evidence_id=evidence_id,
                identity_id=identity_id,
                observation_date=_as_date(record.data.get("observation_date"), observation_fallback),
                publication_date=record.publication_date,
                native_price=price,
                price_min=float(record.data["price_low"]) if record.data.get("price_low") is not None else None,
                price_max=float(record.data["price_high"]) if record.data.get("price_high") is not None else None,
                price_midpoint=float(record.data["price_midpoint"]) if record.data.get("price_midpoint") is not None else price,
                currency=identity.currency,
                unit=identity.unit,
                confidence=evidence_weight(source.reliability_level, complete),
                verification_status="AUTOMATED",
                raw_payload=jsonable_encoder(record.data),
            )
            qualified += 1
            impacted_identity_ids.add(identity_id)

        for identity_id in impacted_identity_ids:
            self.rebuild_reference(identity_id)

        successful = result.state in {RunState.SUCCESS, RunState.PARTIAL}
        changed = any(evidence_change_flags)
        self.market_store.update_source_check(
            connector.source_id,
            checked_at=checked_at,
            success=successful,
            changed=changed,
            health_status=result.state.value,
        )
        if not successful and result.error:
            self.market_store.add_quality_issue(
                source_id=connector.source_id,
                run_id=run_id,
                severity="WARNING" if result.state in {RunState.SOURCE_CHANGED, RunState.MANUAL_REVIEW_REQUIRED} else "ERROR",
                issue_type=result.state.value,
                message=result.error,
            )
        self.market_store.finish_run(
            run_id,
            finished_at=_now(),
            status=result.state.value,
            extraction_method=source.collection_method,
            records_seen=len(result.records),
            records_qualified=qualified,
            source_changed=result.state == RunState.SOURCE_CHANGED,
            error_class=result.state.value if not successful else None,
            error_message=result.error,
            evidence_prefix=result.raw_ref.path if result.raw_ref else None,
            metadata={
                "raw_evidence_count": len(result.raw_refs),
                "raw_hashes": [ref.content_hash for ref in result.raw_refs],
                "content_changed": changed,
            },
        )
        return result
