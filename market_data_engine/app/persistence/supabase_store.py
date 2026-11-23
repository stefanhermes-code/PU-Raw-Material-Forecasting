from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx


class SupabaseMarketStore:
    """Server-side persistence adapter for the private forecasting schema."""

    def __init__(self, supabase_url: str, secret_key: str, *, client=None, timeout: float = 30.0):
        self.supabase_url = supabase_url.rstrip("/")
        self.secret_key = secret_key
        self.client = client or httpx.Client()
        self.timeout = timeout

    def _headers(self, *, write: bool = False, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "apikey": self.secret_key,
            "Content-Type": "application/json",
            "Accept-Profile": "forecasting",
        }
        if write:
            headers["Content-Profile"] = "forecasting"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(self, method: str, table: str, *, params=None, payload=None, prefer=None):
        response = self.client.request(
            method,
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(write=method != "GET", prefer=prefer),
            params=params,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def begin_run(self, source_id: str) -> str:
        response = self._request(
            "POST",
            "acquisition_run",
            payload={"source_id": source_id, "status": "RUNNING"},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Supabase did not return the created acquisition run")
        return str(rows[0]["run_id"])

    def finish_run(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        status: str,
        extraction_method: str | None = None,
        records_seen: int = 0,
        records_qualified: int = 0,
        source_changed: bool = False,
        error_class: str | None = None,
        error_message: str | None = None,
        evidence_prefix: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "finished_at": finished_at.isoformat(),
            "status": status,
            "extraction_method": extraction_method,
            "records_seen": records_seen,
            "records_qualified": records_qualified,
            "source_changed": source_changed,
            "error_class": error_class,
            "error_message": error_message,
            "evidence_prefix": evidence_prefix,
            "metadata": metadata or {},
        }
        self._request("PATCH", "acquisition_run", params={"run_id": f"eq.{run_id}"}, payload=payload, prefer="return=minimal")

    def upsert_raw_evidence(
        self,
        *,
        source_id: str,
        run_id: str,
        collected_at: datetime,
        publication_date: datetime | None,
        source_url: str,
        content_hash: str,
        mime_type: str,
        storage_path: str,
        extraction_method: str,
        rights_class: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        params = {
            "source_id": f"eq.{source_id}",
            "content_hash": f"eq.{content_hash}",
            "select": "evidence_id",
            "limit": "1",
        }
        existing = self._request("GET", "raw_evidence", params=params).json()
        if existing:
            return str(existing[0]["evidence_id"]), False
        response = self._request(
            "POST",
            "raw_evidence",
            payload={
                "source_id": source_id,
                "run_id": run_id,
                "collected_at": collected_at.isoformat(),
                "publication_date": publication_date.isoformat() if publication_date else None,
                "source_url": source_url,
                "content_hash": content_hash,
                "mime_type": mime_type,
                "storage_path": storage_path,
                "extraction_method": extraction_method,
                "rights_class": rights_class,
                "metadata": metadata or {},
            },
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Supabase did not return the created raw evidence row")
        return str(rows[0]["evidence_id"]), True

    def ensure_identity(self, identity: dict[str, Any]) -> str:
        key = str(identity["identity_key"])
        existing = self._request(
            "GET",
            "market_identity",
            params={"identity_key": f"eq.{key}", "select": "identity_id,identity_key", "limit": "1"},
        ).json()
        if existing:
            return str(existing[0]["identity_id"])
        response = self._request(
            "POST",
            "market_identity",
            payload=identity,
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Supabase did not return the created market identity")
        return str(rows[0]["identity_id"])

    def add_price_observation(
        self,
        *,
        source_id: str,
        run_id: str,
        evidence_id: str,
        identity_id: str,
        observation_date: date,
        publication_date: datetime | None,
        native_price: float,
        currency: str,
        unit: str,
        confidence: float,
        raw_payload: dict[str, Any],
        price_min: float | None = None,
        price_max: float | None = None,
        price_midpoint: float | None = None,
        verification_status: str = "AUTOMATED",
    ) -> None:
        self._request(
            "POST",
            "price_observation",
            payload={
                "source_id": source_id,
                "run_id": run_id,
                "evidence_id": evidence_id,
                "identity_id": identity_id,
                "observation_date": observation_date.isoformat(),
                "publication_date": publication_date.isoformat() if publication_date else None,
                "native_price": native_price,
                "price_min": price_min,
                "price_max": price_max,
                "price_midpoint": price_midpoint,
                "currency": currency,
                "unit": unit,
                "confidence": confidence,
                "verification_status": verification_status,
                "raw_payload": raw_payload,
            },
            prefer="resolution=ignore-duplicates,return=minimal",
        )

    def add_quality_issue(
        self,
        *,
        severity: str,
        issue_type: str,
        message: str,
        source_id: str | None = None,
        run_id: str | None = None,
        observation_id: str | None = None,
    ) -> None:
        self._request(
            "POST",
            "quality_issue",
            payload={
                "source_id": source_id,
                "run_id": run_id,
                "observation_id": observation_id,
                "severity": severity,
                "issue_type": issue_type,
                "message": message,
            },
            prefer="return=minimal",
        )

    def update_source_check(
        self,
        source_id: str,
        *,
        checked_at: datetime,
        success: bool,
        changed: bool,
        health_status: str,
    ) -> None:
        payload: dict[str, Any] = {
            "last_checked_at": checked_at.isoformat(),
            "health_status": health_status,
            "updated_at": checked_at.isoformat(),
        }
        if changed:
            payload["last_changed_at"] = checked_at.isoformat()
        if success:
            payload["last_successful_collection_at"] = checked_at.isoformat()
            payload["consecutive_failures"] = 0
        self._request("PATCH", "source_registry", params={"source_id": f"eq.{source_id}"}, payload=payload, prefer="return=minimal")

    def list_source_state(self) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "source_registry",
            params={
                "select": "source_id,active,rights_class,refresh_frequency,last_checked_at,last_changed_at,last_successful_collection_at,health_status,requires_auth",
                "order": "source_id.asc",
            },
        )
        return list(response.json())

    def list_price_observations(self, *, identity_id: str | None = None) -> list[dict[str, Any]]:
        params = {"select": "*", "order": "observation_date.asc,created_at.asc"}
        if identity_id:
            params["identity_id"] = f"eq.{identity_id}"
        return list(self._request("GET", "price_observation", params=params).json())

    def list_market_identities(self) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                "market_identity",
                params={"select": "*", "order": "product_family.asc,identity_key.asc"},
            ).json()
        )

    def list_market_references(self) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                "market_reference",
                params={"select": "*", "order": "reference_date.asc,created_at.asc"},
            ).json()
        )

    def add_market_reference(
        self,
        *,
        identity_id: str,
        reference_date: date,
        reference_value: float,
        currency: str,
        unit: str,
        source_ids: list[str],
        observation_ids: list[str],
        method: str,
        confidence: float | None = None,
        dispersion: float | None = None,
        normalization_scope: dict[str, Any] | None = None,
    ) -> None:
        self._request(
            "POST",
            "market_reference",
            params={"on_conflict": "identity_id,reference_date,method"},
            payload={
                "identity_id": identity_id,
                "reference_date": reference_date.isoformat(),
                "reference_value": reference_value,
                "currency": currency,
                "unit": unit,
                "source_count": len(set(source_ids)),
                "source_ids": sorted(set(source_ids)),
                "observation_ids": observation_ids,
                "method": method,
                "dispersion": dispersion,
                "confidence": confidence,
                "normalization_scope": normalization_scope or {},
            },
            prefer="resolution=merge-duplicates,return=minimal",
        )
