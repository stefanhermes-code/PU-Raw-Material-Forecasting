from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os, tempfile

@dataclass(frozen=True)
class RawEvidenceRef:
    content_hash: str
    path: str

class RawStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes, content_type: str = "application/octet-stream") -> RawEvidenceRef:
        digest = sha256(payload).hexdigest()
        path = self.root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(payload)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return RawEvidenceRef(digest, str(path))


def create_raw_store(settings):
    """Return the configured raw-evidence backend without exposing cloud secrets to callers."""
    if settings.raw_store_backend.lower() != "supabase":
        return RawStore(settings.raw_store_root)
    settings.validate_cloud_storage()
    from app.ingestion.supabase_raw_store import SupabaseRawStore
    return SupabaseRawStore(
        settings.supabase_url,
        settings.supabase_secret_key,
        settings.supabase_evidence_bucket,
    )
