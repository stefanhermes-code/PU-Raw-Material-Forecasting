from dataclasses import dataclass, field
import os


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in {None, ""} else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql+psycopg://forecast:forecast@db:5432/forecasting"))
    raw_store_root: str = field(default_factory=lambda: _env("RAW_STORE_ROOT", "./raw_store"))
    raw_store_backend: str = field(default_factory=lambda: _env("RAW_STORE_BACKEND", "filesystem"))
    supabase_url: str | None = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_secret_key: str | None = field(default_factory=lambda: _env("SUPABASE_SECRET_KEY"))
    supabase_evidence_bucket: str = field(default_factory=lambda: _env("SUPABASE_EVIDENCE_BUCKET", "raw-market-evidence"))

    def validate_cloud_storage(self) -> None:
        if self.raw_store_backend.lower() != "supabase":
            return
        missing = []
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_secret_key:
            missing.append("SUPABASE_SECRET_KEY")
        if missing:
            raise ValueError("Missing required Supabase storage settings: " + ", ".join(missing))
