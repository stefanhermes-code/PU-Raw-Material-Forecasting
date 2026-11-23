from __future__ import annotations

from hashlib import sha256

import httpx

from app.ingestion.raw_store import RawEvidenceRef


class SupabaseRawStore:
    """Immutable content-addressed raw evidence stored in a private Supabase bucket."""

    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        bucket: str = "raw-market-evidence",
        *,
        client=None,
        timeout: float = 30.0,
    ):
        self.supabase_url = supabase_url.rstrip("/")
        self.secret_key = secret_key
        self.bucket = bucket
        self.client = client or httpx.Client()
        self.timeout = timeout

    def put(self, payload: bytes, content_type: str = "application/octet-stream") -> RawEvidenceRef:
        digest = sha256(payload).hexdigest()
        object_path = f"{digest[:2]}/{digest}"
        url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{object_path}"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "apikey": self.secret_key,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        response = self.client.post(url, headers=headers, content=payload, timeout=self.timeout)
        if response.status_code != 409:
            response.raise_for_status()
        return RawEvidenceRef(digest, f"supabase://{self.bucket}/{object_path}")
