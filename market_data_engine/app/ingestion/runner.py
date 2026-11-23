from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import RunState
from app.domain.schemas import SourceItem
from app.ingestion.raw_store import RawEvidenceRef, RawStore
from app.sources.registry import SourceRegistry, ensure_source_publishable


class ManualReviewRequired(RuntimeError):
    """Raised when fetched evidence is readable but requires human identity/quality review."""


@dataclass
class IngestionRunResult:
    source_id: str
    state: RunState
    raw_ref: RawEvidenceRef | None = None
    raw_refs: list[RawEvidenceRef] = field(default_factory=list)
    raw_by_url: dict[str, RawEvidenceRef] = field(default_factory=dict)
    records: list = field(default_factory=list)
    error: str | None = None


class IngestionRunner:
    def __init__(self, registry: SourceRegistry, raw_root=None, *, raw_store=None):
        self.registry = registry
        if raw_store is not None:
            self.raw_store = raw_store
        elif raw_root is not None:
            self.raw_store = RawStore(raw_root)
        else:
            raise ValueError("raw_root or raw_store is required")

    def _legacy_run(self, connector) -> IngestionRunResult:
        payload = connector.fetch()
        raw_ref = self.raw_store.put(payload, "text/html")
        records = connector.extract_html(payload.decode("utf-8", errors="replace"), connector.source_url)
        if not records:
            if getattr(connector, "allow_empty_parse", False):
                return IngestionRunResult(
                    connector.source_id,
                    RunState.SUCCESS,
                    raw_ref=raw_ref,
                    raw_refs=[raw_ref],
                    raw_by_url={connector.source_url: raw_ref},
                    records=[],
                )
            return IngestionRunResult(
                connector.source_id,
                RunState.SOURCE_CHANGED,
                raw_ref=raw_ref,
                raw_refs=[raw_ref],
                raw_by_url={connector.source_url: raw_ref},
                records=[],
            )
        return IngestionRunResult(
            connector.source_id,
            RunState.SUCCESS,
            raw_ref=raw_ref,
            raw_refs=[raw_ref],
            raw_by_url={connector.source_url: raw_ref},
            records=records,
        )

    def run(self, connector) -> IngestionRunResult:
        try:
            ensure_source_publishable(self.registry, connector.source_id)
        except PermissionError as exc:
            return IngestionRunResult(connector.source_id, RunState.RIGHTS_BLOCKED, error=str(exc))

        if not (hasattr(connector, "discover") and hasattr(connector, "fetch_item")):
            try:
                return self._legacy_run(connector)
            except ManualReviewRequired as exc:
                return IngestionRunResult(connector.source_id, RunState.MANUAL_REVIEW_REQUIRED, error=str(exc))
            except Exception as exc:
                return IngestionRunResult(connector.source_id, RunState.FAILED, error=str(exc))

        try:
            items = connector.discover()
        except ManualReviewRequired as exc:
            return IngestionRunResult(connector.source_id, RunState.MANUAL_REVIEW_REQUIRED, error=str(exc))
        except Exception as exc:
            return IngestionRunResult(connector.source_id, RunState.FAILED, error=f"discovery failed: {exc}")
        if not items:
            return IngestionRunResult(connector.source_id, RunState.SOURCE_CHANGED, error="discovery returned no source items")

        raw_refs: list[RawEvidenceRef] = []
        raw_by_url: dict[str, RawEvidenceRef] = {}
        records: list = []
        errors: list[str] = []
        manual_review_errors: list[str] = []
        successful_fetches = 0
        for item in items:
            if not isinstance(item, SourceItem):
                item = SourceItem.model_validate(item)
            try:
                payload = connector.fetch_item(item)
                successful_fetches += 1
                raw_ref = self.raw_store.put(payload, "text/html")
                raw_refs.append(raw_ref)
                raw_by_url[item.source_url] = raw_ref
                parsed = connector.extract_html(payload.decode("utf-8", errors="replace"), item.source_url)
                if parsed:
                    records.extend(parsed)
                elif not getattr(connector, "allow_empty_parse", False):
                    errors.append(f"no records parsed from {item.source_url}")
                # A connector that allows empty parses has already said that a page carrying no
                # price is not a fault, and the two single-page branches honour that. This one did
                # not, so a run that got the price it came for still ended PARTIAL because another
                # item in the same discovery - a listing page that by design holds no table - had
                # nothing to give. `longzhong_pmdi` and `longzhong_polyol` recorded 156 such runs
                # in the thirty days to 5 November 2026, every one of them for this reason alone,
                # while both delivered a price that same day: two healthy feeds reading as dead on
                # the quiet list, and `last_successful_collection` never moving for either.
            except ManualReviewRequired as exc:
                manual_review_errors.append(f"{item.source_url}: {exc}")
            except Exception as exc:
                errors.append(f"{item.source_url}: {exc}")

        first_raw = raw_refs[0] if raw_refs else None
        all_errors = [*manual_review_errors, *errors]
        error_text = "; ".join(all_errors) if all_errors else None
        if records and all_errors:
            state = RunState.PARTIAL
        elif records:
            state = RunState.SUCCESS
        elif manual_review_errors:
            state = RunState.MANUAL_REVIEW_REQUIRED
        elif successful_fetches and getattr(connector, "allow_empty_parse", False):
            state = RunState.SUCCESS
        elif successful_fetches:
            state = RunState.SOURCE_CHANGED
        else:
            state = RunState.FAILED
        return IngestionRunResult(
            connector.source_id,
            state,
            raw_ref=first_raw,
            raw_refs=raw_refs,
            raw_by_url=raw_by_url,
            records=records,
            error=error_text,
        )
