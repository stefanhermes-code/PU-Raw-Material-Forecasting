from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
from re import sub
from typing import Protocol
from uuid import uuid4

import pandas as pd
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, insert, select
from sqlalchemy.engine import Engine

from raw_material_forecasting.platform_admin.models import EntitlementScope, intersect_scope, scope_contains
from raw_material_forecasting.platform_admin.service import (
    ConfigurationError,
    EntitlementViolation,
    PlatformAdminService,
)
from raw_material_forecasting.market_basis import INCOTERM_LABEL, display_basis


@dataclass(frozen=True)
class GovernedForecastRow:
    as_of_date: date
    chemical: str
    geography: str
    incoterm: str
    packaging: str
    currency: str
    current_market_price: float | None
    current_market_date: date | None
    forecast_1m: float | None = None
    forecast_2m: float | None = None
    forecast_3m: float | None = None
    lower_confidence_bound: float | None = None
    upper_confidence_bound: float | None = None
    forecast_confidence: str | None = None
    forecast_change_vs_previous: float | None = None


@dataclass(frozen=True)
class GovernedForecastRun:
    run_id: str
    generated_at: datetime
    model_version: str
    rows: tuple[GovernedForecastRow, ...]

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        if not self.model_version.strip():
            raise ValueError("model_version is required")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")


@dataclass(frozen=True)
class OutputGenerationRecord:
    generation_id: str
    company_id: str
    delivery_profile_id: str | None
    run_id: str
    model_version: str
    output_type: str
    output_format: str
    status: str
    file_path: str
    row_count: int
    generated_at: datetime
    generated_by: str
    error_message: str | None


_generation_metadata = MetaData()
_output_generations = Table(
    "pa_output_generation",
    _generation_metadata,
    Column("generation_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("delivery_profile_id", String),
    Column("run_id", String, nullable=False),
    Column("model_version", String, nullable=False),
    Column("output_type", String, nullable=False),
    Column("output_format", String, nullable=False),
    Column("status", String, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("generated_by", String, nullable=False),
    Column("error_message", Text),
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class OutputGenerationRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        _generation_metadata.create_all(engine)

    def add(self, record: OutputGenerationRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(_output_generations).values(
                generation_id=record.generation_id,
                company_id=record.company_id,
                delivery_profile_id=record.delivery_profile_id,
                run_id=record.run_id,
                model_version=record.model_version,
                output_type=record.output_type,
                output_format=record.output_format,
                status=record.status,
                file_path=record.file_path,
                row_count=record.row_count,
                generated_at=record.generated_at,
                generated_by=record.generated_by,
                error_message=record.error_message,
            ))

    def get(self, generation_id: str) -> OutputGenerationRecord | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_output_generations).where(_output_generations.c.generation_id == generation_id)
            ).mappings().first()
        if row is None:
            return None
        return OutputGenerationRecord(
            generation_id=row["generation_id"],
            company_id=row["company_id"],
            delivery_profile_id=row["delivery_profile_id"],
            run_id=row["run_id"],
            model_version=row["model_version"],
            output_type=row["output_type"],
            output_format=row["output_format"],
            status=row["status"],
            file_path=row["file_path"],
            row_count=int(row["row_count"]),
            generated_at=_as_utc(row["generated_at"]),
            generated_by=row["generated_by"],
            error_message=row["error_message"],
        )

    def list_records(self, company_id: str | None = None) -> list[OutputGenerationRecord]:
        query = select(_output_generations)
        if company_id:
            query = query.where(_output_generations.c.company_id == company_id)
        query = query.order_by(_output_generations.c.generated_at.desc(), _output_generations.c.generation_id.desc())
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [OutputGenerationRecord(
            generation_id=row["generation_id"],
            company_id=row["company_id"],
            delivery_profile_id=row["delivery_profile_id"],
            run_id=row["run_id"],
            model_version=row["model_version"],
            output_type=row["output_type"],
            output_format=row["output_format"],
            status=row["status"],
            file_path=row["file_path"],
            row_count=int(row["row_count"]),
            generated_at=_as_utc(row["generated_at"]),
            generated_by=row["generated_by"],
            error_message=row["error_message"],
        ) for row in rows]


@dataclass(frozen=True)
class DeliveryAttemptRecord:
    delivery_id: str
    generation_id: str
    company_id: str
    delivery_profile_id: str
    channel: str
    destinations: tuple[str, ...]
    artifact_path: str
    delivered_location: str | None
    status: str
    attempted_at: datetime
    completed_at: datetime | None
    delivered_by: str
    error_message: str | None


_delivery_metadata = MetaData()
_output_deliveries = Table(
    "pa_output_delivery",
    _delivery_metadata,
    Column("delivery_id", String, primary_key=True),
    Column("generation_id", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("delivery_profile_id", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("destinations_json", Text, nullable=False),
    Column("artifact_path", Text, nullable=False),
    Column("delivered_location", Text),
    Column("status", String, nullable=False),
    Column("attempted_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("delivered_by", String, nullable=False),
    Column("error_message", Text),
)


class DeliveryAttemptRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        _delivery_metadata.create_all(engine)

    def add(self, record: DeliveryAttemptRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(_output_deliveries).values(
                delivery_id=record.delivery_id,
                generation_id=record.generation_id,
                company_id=record.company_id,
                delivery_profile_id=record.delivery_profile_id,
                channel=record.channel,
                destinations_json=json.dumps(list(record.destinations)),
                artifact_path=record.artifact_path,
                delivered_location=record.delivered_location,
                status=record.status,
                attempted_at=record.attempted_at,
                completed_at=record.completed_at,
                delivered_by=record.delivered_by,
                error_message=record.error_message,
            ))

    def list_records(self, company_id: str | None = None) -> list[DeliveryAttemptRecord]:
        query = select(_output_deliveries)
        if company_id:
            query = query.where(_output_deliveries.c.company_id == company_id)
        query = query.order_by(_output_deliveries.c.attempted_at.desc(), _output_deliveries.c.delivery_id.desc())
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [DeliveryAttemptRecord(
            delivery_id=row["delivery_id"],
            generation_id=row["generation_id"],
            company_id=row["company_id"],
            delivery_profile_id=row["delivery_profile_id"],
            channel=row["channel"],
            destinations=tuple(json.loads(row["destinations_json"])),
            artifact_path=row["artifact_path"],
            delivered_location=row["delivered_location"],
            status=row["status"],
            attempted_at=_as_utc(row["attempted_at"]),
            completed_at=_as_utc(row["completed_at"]) if row["completed_at"] is not None else None,
            delivered_by=row["delivered_by"],
            error_message=row["error_message"],
        ) for row in rows]


EXPORT_COLUMNS = [
    "As Of Date",
    "Company ID",
    "Chemical",
    "Geography",
    # A customer is offered only markets priced on an Incoterm, so this column only ever holds one,
    # and is named for it - the same word as both market selectors. It was "Price basis" for one
    # release, while the selectors still offered producer list prices and seller quotes as if they
    # were delivery terms. Nothing has ever been delivered, so no customer system reads it by name.
    INCOTERM_LABEL,
    "Packaging",
    "Currency",
    "Current Market Price",
    "Current Market Date",
    "Forecast 1M",
    "Forecast 2M",
    "Forecast 3M",
    "Lower Confidence Bound",
    "Upper Confidence Bound",
    "Forecast Confidence",
    "Forecast Change vs Previous",
    "Forecast Run ID",
    "Model Version",
    "Generated At",
]


def _allows(allowed: frozenset[str], value: str) -> bool:
    return "*" in allowed or value in allowed


def _safe_path_segment(value: str) -> str:
    cleaned = sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return cleaned.strip("-.") or "unnamed"


def _format_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:1000]


class EmailDeliveryAdapter(Protocol):
    def send(
        self,
        artifact_path: Path,
        recipients: tuple[str, ...],
        *,
        subject: str,
        body: str,
    ) -> str:
        ...


class SecureLocationAdapter(Protocol):
    def deliver(self, artifact_path: Path, *, company_id: str, profile_id: str) -> str:
        ...


class LocalSecureLocationAdapter:
    def __init__(self, root: Path):
        self.root = Path(root)

    def deliver(self, artifact_path: Path, *, company_id: str, profile_id: str) -> str:
        source = Path(artifact_path)
        if not source.exists() or not source.is_file() or source.stat().st_size <= 0:
            raise RuntimeError("validated artifact is missing or empty")
        destination_dir = self.root / _safe_path_segment(company_id) / _safe_path_segment(profile_id)
        destination_dir.mkdir(parents=True, exist_ok=True)
        final_path = destination_dir / source.name
        temp_path = destination_dir / f".{source.name}.tmp"
        shutil.copy2(source, temp_path)
        temp_path.replace(final_path)
        return str(final_path)


class DeliveryExecutionService:
    def __init__(
        self,
        platform_admin: PlatformAdminService,
        generations: OutputGenerationRepository,
        deliveries: DeliveryAttemptRepository,
        *,
        email_adapter: EmailDeliveryAdapter | None = None,
        secure_location_adapter: SecureLocationAdapter | None = None,
        now_fn=None,
    ):
        self.platform_admin = platform_admin
        self.generations = generations
        self.deliveries = deliveries
        self.email_adapter = email_adapter
        self.secure_location_adapter = secure_location_adapter
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return _as_utc(self._now_fn())

    def deliver(self, generation_id: str, *, delivered_by: str) -> list[DeliveryAttemptRecord]:
        actor = delivered_by.strip()
        if not actor:
            raise ConfigurationError("delivered_by is required")
        generation = self.generations.get(generation_id)
        if generation is None:
            raise ConfigurationError(f"unknown generation: {generation_id}")
        if generation.status != "VALIDATED":
            raise EntitlementViolation("only VALIDATED output generations can be delivered")
        artifact = Path(generation.file_path)
        if not artifact.exists() or not artifact.is_file() or artifact.stat().st_size <= 0:
            raise ConfigurationError("validated output artifact is missing or empty")
        if not generation.delivery_profile_id:
            raise EntitlementViolation("delivery requires a delivery profile")

        profile = self.platform_admin.repo.get_delivery_profile(generation.delivery_profile_id)
        if profile is None or profile.company_id != generation.company_id:
            raise EntitlementViolation("delivery profile is not available for this company")
        if not profile.active:
            raise EntitlementViolation("delivery profile is inactive")

        now = self._now()
        company = self.platform_admin.repo.get_company(generation.company_id)
        if company is None or company.status not in {"ACTIVE", "TRIAL"}:
            raise EntitlementViolation("company is not active for delivery")
        today = now.date()
        if today < company.contract_effective_from or (
            company.contract_effective_to is not None and today > company.contract_effective_to
        ):
            raise EntitlementViolation("company contract is not active for delivery")

        company_scope = self.platform_admin.effective_company_scope(generation.company_id, at=now)
        if not scope_contains(company_scope, profile.scope):
            raise EntitlementViolation("delivery profile scope exceeds the current company entitlement")
        if not _allows(company_scope.report_formats, generation.output_format):
            raise EntitlementViolation("output format is no longer included in the company entitlement")
        if not _allows(profile.formats, generation.output_format):
            raise EntitlementViolation("output format is not included in the delivery profile")
        if not _allows(company_scope.delivery_frequencies, profile.frequency):
            raise EntitlementViolation("delivery frequency is no longer included in the company entitlement")
        for channel in profile.channels:
            if not _allows(company_scope.delivery_channels, channel):
                raise EntitlementViolation("delivery channel is no longer included in the company entitlement")

        recipients = []
        for recipient_id in sorted(profile.recipient_ids):
            recipient = self.platform_admin.repo.get_recipient(recipient_id)
            if recipient is None or recipient.company_id != generation.company_id:
                raise EntitlementViolation("delivery profile includes a missing or cross-company recipient")
            recipients.append(recipient.email)
        recipient_emails = tuple(recipients)
        if "Email" in profile.channels and not recipient_emails:
            raise EntitlementViolation("Email delivery profile has no recipients")

        results: list[DeliveryAttemptRecord] = []
        for channel in sorted(profile.channels):
            attempted_at = self._now()
            status = "FAILED"
            delivered_location: str | None = None
            destinations: tuple[str, ...] = ()
            error_message: str | None = None
            try:
                if channel == "Email":
                    destinations = recipient_emails
                    if self.email_adapter is None:
                        status = "NOT_CONFIGURED"
                        error_message = "Email delivery transport is not configured"
                    else:
                        delivered_location = self.email_adapter.send(
                            artifact,
                            recipient_emails,
                            subject=f"PU Raw Material Forecasting - {profile.name} - {generation.run_id}",
                            body=(
                                "HTC Global governed forecast output. "
                                f"Run {generation.run_id}; model {generation.model_version}."
                            ),
                        )
                        status = "DELIVERED"
                elif channel == "Secure location":
                    if self.secure_location_adapter is None:
                        status = "NOT_CONFIGURED"
                        error_message = "Secure location delivery transport is not configured"
                    else:
                        delivered_location = self.secure_location_adapter.deliver(
                            artifact, company_id=generation.company_id, profile_id=profile.profile_id
                        )
                        destinations = (delivered_location,)
                        status = "DELIVERED"
                else:
                    error_message = f"Unsupported delivery channel: {channel}"
            except Exception as exc:
                status = "FAILED"
                error_message = _format_error(exc)

            completed_at = self._now()
            record = DeliveryAttemptRecord(
                delivery_id=str(uuid4()),
                generation_id=generation.generation_id,
                company_id=generation.company_id,
                delivery_profile_id=profile.profile_id,
                channel=channel,
                destinations=destinations,
                artifact_path=str(artifact),
                delivered_location=delivered_location,
                status=status,
                attempted_at=attempted_at,
                completed_at=completed_at,
                delivered_by=actor,
                error_message=error_message,
            )
            self.deliveries.add(record)
            results.append(record)
        return results


class DataExportService:
    def __init__(
        self,
        platform_admin: PlatformAdminService,
        history: OutputGenerationRepository,
        output_root: Path,
        *,
        now_fn=None,
    ):
        self.platform_admin = platform_admin
        self.history = history
        self.output_root = Path(output_root)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return _as_utc(self._now_fn())

    def generate(
        self,
        run: GovernedForecastRun,
        *,
        company_id: str,
        output_format: str,
        generated_by: str,
        delivery_profile_id: str | None = None,
    ) -> OutputGenerationRecord:
        generation_id = str(uuid4())
        output_format = output_format.strip().upper()
        generated_at = self._now()
        if not generated_by.strip():
            raise ConfigurationError("generated_by is required")

        try:
            scope = self._effective_output_scope(
                company_id=company_id,
                output_format=output_format,
                delivery_profile_id=delivery_profile_id,
                at=generated_at,
            )
            frame = self._build_customer_frame(run, company_id=company_id, scope=scope, generated_at=generated_at)
            if frame.empty:
                raise EntitlementViolation("governed run contains no rows within the active output entitlement")

            final_path, temp_path = self._paths(
                company_id=company_id,
                run_id=run.run_id,
                generation_id=generation_id,
                output_format=output_format,
            )
            final_path.parent.mkdir(parents=True, exist_ok=True)
            self._write(frame, temp_path, output_format)
            self._validate_file(
                temp_path,
                output_format=output_format,
                expected_rows=len(frame),
                run=run,
                company_id=company_id,
            )
            temp_path.replace(final_path)
            record = OutputGenerationRecord(
                generation_id=generation_id,
                company_id=company_id,
                delivery_profile_id=delivery_profile_id,
                run_id=run.run_id,
                model_version=run.model_version,
                output_type="DATA_EXPORT",
                output_format=output_format,
                status="VALIDATED",
                file_path=str(final_path),
                row_count=len(frame),
                generated_at=generated_at,
                generated_by=generated_by.strip(),
                error_message=None,
            )
            self.history.add(record)
            return record
        except Exception as exc:
            failed = OutputGenerationRecord(
                generation_id=generation_id,
                company_id=company_id,
                delivery_profile_id=delivery_profile_id,
                run_id=run.run_id,
                model_version=run.model_version,
                output_type="DATA_EXPORT",
                output_format=output_format,
                status="FAILED",
                file_path="",
                row_count=0,
                generated_at=generated_at,
                generated_by=generated_by.strip(),
                error_message=_format_error(exc),
            )
            self.history.add(failed)
            raise

    def _effective_output_scope(
        self,
        *,
        company_id: str,
        output_format: str,
        delivery_profile_id: str | None,
        at: datetime,
    ) -> EntitlementScope:
        if output_format not in {"CSV", "XLSX"}:
            raise ConfigurationError("output format must be CSV or XLSX")
        company = self.platform_admin.repo.get_company(company_id)
        if company is None:
            raise ConfigurationError(f"unknown company: {company_id}")
        if company.status not in {"ACTIVE", "TRIAL"}:
            raise EntitlementViolation("company is not active for output generation")
        today = at.date()
        if today < company.contract_effective_from or (
            company.contract_effective_to is not None and today > company.contract_effective_to
        ):
            raise EntitlementViolation("company contract is not active for output generation")

        company_scope = self.platform_admin.effective_company_scope(company_id, at=at)
        if not _allows(company_scope.report_formats, output_format):
            raise EntitlementViolation("output format is not included in the company entitlement")

        if delivery_profile_id is None:
            return company_scope

        profile = self.platform_admin.repo.get_delivery_profile(delivery_profile_id)
        if profile is None or profile.company_id != company_id:
            raise EntitlementViolation("delivery profile is not available for this company")
        if not profile.active:
            raise EntitlementViolation("delivery profile is inactive")
        if not _allows(profile.formats, output_format):
            raise EntitlementViolation("output format is not included in the delivery profile")
        return intersect_scope(company_scope, profile.scope)

    def _build_customer_frame(
        self,
        run: GovernedForecastRun,
        *,
        company_id: str,
        scope: EntitlementScope,
        generated_at: datetime,
    ) -> pd.DataFrame:
        rows: list[dict] = []
        current_market_allowed = _allows(scope.features, "current_market")
        forecast_allowed = _allows(scope.features, "forecast")
        if not current_market_allowed and not forecast_allowed:
            raise EntitlementViolation("output entitlement includes neither current market nor forecast content")
        for row in run.rows:
            if not (
                _allows(scope.chemicals, row.chemical)
                and _allows(scope.geographies, row.geography)
                and _allows(scope.incoterms, row.incoterm)
                and _allows(scope.packaging, row.packaging)
            ):
                continue
            rows.append({
                "As Of Date": row.as_of_date.isoformat(),
                "Company ID": company_id,
                "Chemical": row.chemical,
                "Geography": row.geography,
                INCOTERM_LABEL: display_basis(row.incoterm),
                "Packaging": row.packaging,
                "Currency": row.currency,
                "Current Market Price": row.current_market_price if current_market_allowed else None,
                "Current Market Date": row.current_market_date.isoformat() if current_market_allowed and row.current_market_date else None,
                "Forecast 1M": row.forecast_1m if forecast_allowed and scope.forecast_horizon_months >= 1 else None,
                "Forecast 2M": row.forecast_2m if forecast_allowed and scope.forecast_horizon_months >= 2 else None,
                "Forecast 3M": row.forecast_3m if forecast_allowed and scope.forecast_horizon_months >= 3 else None,
                "Lower Confidence Bound": row.lower_confidence_bound if forecast_allowed else None,
                "Upper Confidence Bound": row.upper_confidence_bound if forecast_allowed else None,
                "Forecast Confidence": row.forecast_confidence if forecast_allowed else None,
                "Forecast Change vs Previous": row.forecast_change_vs_previous if forecast_allowed else None,
                "Forecast Run ID": run.run_id,
                "Model Version": run.model_version,
                "Generated At": generated_at.isoformat(),
            })
        return pd.DataFrame(rows, columns=EXPORT_COLUMNS)

    def _paths(
        self,
        *,
        company_id: str,
        run_id: str,
        generation_id: str,
        output_format: str,
    ) -> tuple[Path, Path]:
        suffix = ".csv" if output_format == "CSV" else ".xlsx"
        folder = self.output_root / _safe_path_segment(company_id) / _safe_path_segment(run_id)
        filename = f"{_safe_path_segment(company_id)}_{_safe_path_segment(run_id)}_{generation_id[:8]}{suffix}"
        final_path = folder / filename
        temp_path = folder / f".{filename}.tmp{suffix}"
        return final_path, temp_path

    @staticmethod
    def _write(frame: pd.DataFrame, path: Path, output_format: str) -> None:
        if output_format == "CSV":
            frame.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            frame.to_excel(path, index=False, engine="openpyxl", sheet_name="Forecast Data")

    @staticmethod
    def _validate_file(
        path: Path,
        *,
        output_format: str,
        expected_rows: int,
        run: GovernedForecastRun,
        company_id: str,
    ) -> None:
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError("generated output file is empty")
        frame = pd.read_csv(path) if output_format == "CSV" else pd.read_excel(path, engine="openpyxl")
        if list(frame.columns) != EXPORT_COLUMNS:
            raise RuntimeError("generated output schema validation failed")
        if len(frame) != expected_rows:
            raise RuntimeError("generated output row-count validation failed")
        if expected_rows <= 0:
            raise RuntimeError("generated output has no customer rows")
        if set(frame["Forecast Run ID"].astype(str)) != {run.run_id}:
            raise RuntimeError("generated output run identity validation failed")
        if set(frame["Model Version"].astype(str)) != {run.model_version}:
            raise RuntimeError("generated output model-version validation failed")
        if set(frame["Company ID"].astype(str)) != {company_id}:
            raise RuntimeError("generated output company validation failed")
