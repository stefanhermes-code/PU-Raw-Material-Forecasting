from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from raw_material_forecasting.platform_store import protect_tables
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from raw_material_forecasting.platform_admin.models import DeliveryProfile
from raw_material_forecasting.platform_admin.service import ConfigurationError, PlatformAdminService
from raw_material_forecasting.reporting_delivery import (
    DataExportService,
    DeliveryExecutionService,
    GovernedForecastRun,
)

UTC = timezone.utc
SUPPORTED_SCHEDULED_FORMATS = frozenset({"CSV", "XLSX"})
TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED"})


@dataclass(frozen=True)
class DeliverySchedule:
    profile_id: str
    company_id: str
    timezone_name: str
    delivery_time: time
    first_delivery_date: date
    next_due_at: datetime
    enabled: bool
    updated_at: datetime


@dataclass(frozen=True)
class ScheduleExecutionRecord:
    execution_id: str
    profile_id: str
    company_id: str
    scheduled_for: datetime
    status: str
    generation_ids: tuple[str, ...]
    started_at: datetime
    completed_at: datetime | None
    last_checked_at: datetime
    error_message: str | None


_schedule_metadata = MetaData()
_delivery_schedules = Table(
    "pa_delivery_schedule",
    _schedule_metadata,
    Column("profile_id", String, primary_key=True),
    Column("company_id", String, nullable=False),
    Column("timezone_name", String, nullable=False),
    Column("delivery_time", String, nullable=False),
    Column("first_delivery_date", Date, nullable=False),
    Column("next_due_at", DateTime(timezone=True), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
_schedule_executions = Table(
    "pa_schedule_execution",
    _schedule_metadata,
    Column("execution_id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("scheduled_for", DateTime(timezone=True), nullable=False),
    Column("status", String, nullable=False),
    Column("generation_ids_json", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("last_checked_at", DateTime(timezone=True), nullable=False),
    Column("error_message", Text),
    UniqueConstraint("profile_id", "scheduled_for", name="uq_pa_schedule_profile_occurrence"),
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigurationError(f"unknown IANA timezone: {name}") from exc


def _local_datetime(schedule: DeliverySchedule, target_date: date) -> datetime:
    return datetime.combine(target_date, schedule.delivery_time, tzinfo=_timezone(schedule.timezone_name))


def initial_occurrence(
    timezone_name: str,
    delivery_time: time,
    first_delivery_date: date,
) -> datetime:
    local = datetime.combine(first_delivery_date, delivery_time, tzinfo=_timezone(timezone_name))
    return local.astimezone(UTC)


def next_occurrence(schedule: DeliverySchedule, frequency: str, after_utc: datetime) -> datetime:
    after_local = _as_utc(after_utc).astimezone(_timezone(schedule.timezone_name))
    if frequency == "Daily":
        target = after_local.date() + timedelta(days=1)
    elif frequency == "Weekly":
        target = after_local.date() + timedelta(days=7)
    elif frequency == "Monthly":
        year = after_local.year + (1 if after_local.month == 12 else 0)
        month = 1 if after_local.month == 12 else after_local.month + 1
        anchor_day = schedule.first_delivery_date.day
        day = min(anchor_day, monthrange(year, month)[1])
        target = date(year, month, day)
    else:
        raise ConfigurationError(f"unsupported delivery frequency: {frequency}")
    return _local_datetime(schedule, target).astimezone(UTC)


def next_future_occurrence(
    schedule: DeliverySchedule,
    frequency: str,
    completed_occurrence: datetime,
    now: datetime,
) -> datetime:
    candidate = next_occurrence(schedule, frequency, completed_occurrence)
    now_utc = _as_utc(now)
    while candidate <= now_utc:
        candidate = next_occurrence(schedule, frequency, candidate)
    return candidate


def effective_schedule_status(
    schedule: DeliverySchedule,
    latest_execution: ScheduleExecutionRecord | None,
    now: datetime,
) -> str:
    if not schedule.enabled:
        return "DISABLED"
    if latest_execution and latest_execution.scheduled_for == schedule.next_due_at:
        if latest_execution.status in {"RUNNING", "WAITING_FOR_RUN"}:
            return latest_execution.status
    if schedule.next_due_at <= _as_utc(now):
        return "DUE"
    return "SCHEDULED"


class DeliveryScheduleRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        _schedule_metadata.create_all(engine)
        protect_tables(engine, "delivery schedule")

    @staticmethod
    def _schedule_from_row(row) -> DeliverySchedule:
        return DeliverySchedule(
            profile_id=row["profile_id"],
            company_id=row["company_id"],
            timezone_name=row["timezone_name"],
            delivery_time=time.fromisoformat(row["delivery_time"]),
            first_delivery_date=row["first_delivery_date"],
            next_due_at=_as_utc(row["next_due_at"]),
            enabled=bool(row["enabled"]),
            updated_at=_as_utc(row["updated_at"]),
        )

    @staticmethod
    def _execution_from_row(row) -> ScheduleExecutionRecord:
        return ScheduleExecutionRecord(
            execution_id=row["execution_id"],
            profile_id=row["profile_id"],
            company_id=row["company_id"],
            scheduled_for=_as_utc(row["scheduled_for"]),
            status=row["status"],
            generation_ids=tuple(json.loads(row["generation_ids_json"])),
            started_at=_as_utc(row["started_at"]),
            completed_at=_as_utc(row["completed_at"]) if row["completed_at"] is not None else None,
            last_checked_at=_as_utc(row["last_checked_at"]),
            error_message=row["error_message"],
        )

    def save_schedule(self, schedule: DeliverySchedule) -> None:
        values = {
            "profile_id": schedule.profile_id,
            "company_id": schedule.company_id,
            "timezone_name": schedule.timezone_name,
            "delivery_time": schedule.delivery_time.isoformat(),
            "first_delivery_date": schedule.first_delivery_date,
            "next_due_at": _as_utc(schedule.next_due_at),
            "enabled": schedule.enabled,
            "updated_at": _as_utc(schedule.updated_at),
        }
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(_delivery_schedules.c.profile_id).where(_delivery_schedules.c.profile_id == schedule.profile_id)
            ).first()
            if exists:
                conn.execute(
                    update(_delivery_schedules)
                    .where(_delivery_schedules.c.profile_id == schedule.profile_id)
                    .values(**values)
                )
            else:
                conn.execute(insert(_delivery_schedules).values(**values))

    def get_schedule(self, profile_id: str) -> DeliverySchedule | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_delivery_schedules).where(_delivery_schedules.c.profile_id == profile_id)
            ).mappings().first()
        return self._schedule_from_row(row) if row else None

    def list_schedules(self, company_id: str | None = None) -> list[DeliverySchedule]:
        query = select(_delivery_schedules)
        if company_id:
            query = query.where(_delivery_schedules.c.company_id == company_id)
        query = query.order_by(_delivery_schedules.c.company_id, _delivery_schedules.c.profile_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._schedule_from_row(row) for row in rows]

    def add_execution(self, record: ScheduleExecutionRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(_schedule_executions).values(
                execution_id=record.execution_id,
                profile_id=record.profile_id,
                company_id=record.company_id,
                scheduled_for=_as_utc(record.scheduled_for),
                status=record.status,
                generation_ids_json=json.dumps(list(record.generation_ids)),
                started_at=_as_utc(record.started_at),
                completed_at=_as_utc(record.completed_at) if record.completed_at else None,
                last_checked_at=_as_utc(record.last_checked_at),
                error_message=record.error_message,
            ))

    def update_execution(self, record: ScheduleExecutionRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(_schedule_executions)
                .where(_schedule_executions.c.execution_id == record.execution_id)
                .values(
                    status=record.status,
                    generation_ids_json=json.dumps(list(record.generation_ids)),
                    started_at=_as_utc(record.started_at),
                    completed_at=_as_utc(record.completed_at) if record.completed_at else None,
                    last_checked_at=_as_utc(record.last_checked_at),
                    error_message=record.error_message,
                )
            )

    def get_execution(self, profile_id: str, scheduled_for: datetime) -> ScheduleExecutionRecord | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_schedule_executions).where(
                    _schedule_executions.c.profile_id == profile_id,
                    _schedule_executions.c.scheduled_for == _as_utc(scheduled_for),
                )
            ).mappings().first()
        return self._execution_from_row(row) if row else None

    def list_executions(
        self,
        company_id: str | None = None,
        profile_id: str | None = None,
    ) -> list[ScheduleExecutionRecord]:
        query = select(_schedule_executions)
        if company_id:
            query = query.where(_schedule_executions.c.company_id == company_id)
        if profile_id:
            query = query.where(_schedule_executions.c.profile_id == profile_id)
        query = query.order_by(
            _schedule_executions.c.scheduled_for.desc(),
            _schedule_executions.c.execution_id.desc(),
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._execution_from_row(row) for row in rows]

    def claim(
        self,
        schedule: DeliverySchedule,
        *,
        now: datetime,
        waiting_retry: timedelta,
        lease_timeout: timedelta,
        force_retry_waiting: bool = False,
    ) -> ScheduleExecutionRecord | None:
        now = _as_utc(now)
        scheduled_for = _as_utc(schedule.next_due_at)
        try:
            with self.engine.begin() as conn:
                row = conn.execute(
                    select(_schedule_executions).where(
                        _schedule_executions.c.profile_id == schedule.profile_id,
                        _schedule_executions.c.scheduled_for == scheduled_for,
                    )
                ).mappings().first()
                if row is None:
                    execution_id = str(uuid4())
                    conn.execute(insert(_schedule_executions).values(
                        execution_id=execution_id,
                        profile_id=schedule.profile_id,
                        company_id=schedule.company_id,
                        scheduled_for=scheduled_for,
                        status="RUNNING",
                        generation_ids_json="[]",
                        started_at=now,
                        completed_at=None,
                        last_checked_at=now,
                        error_message=None,
                    ))
                    return ScheduleExecutionRecord(
                        execution_id, schedule.profile_id, schedule.company_id, scheduled_for,
                        "RUNNING", (), now, None, now, None,
                    )

                existing = self._execution_from_row(row)
                reclaim = False
                if existing.status == "WAITING_FOR_RUN":
                    reclaim = force_retry_waiting or now - existing.last_checked_at >= waiting_retry
                elif existing.status == "RUNNING":
                    reclaim = now - existing.last_checked_at >= lease_timeout
                if not reclaim:
                    return None

                result = conn.execute(
                    update(_schedule_executions)
                    .where(
                        _schedule_executions.c.execution_id == existing.execution_id,
                        _schedule_executions.c.status == existing.status,
                        _schedule_executions.c.last_checked_at == row["last_checked_at"],
                    )
                    .values(status="RUNNING", completed_at=None, last_checked_at=now, error_message=None)
                )
                if result.rowcount != 1:
                    return None
                return ScheduleExecutionRecord(
                    existing.execution_id,
                    existing.profile_id,
                    existing.company_id,
                    existing.scheduled_for,
                    "RUNNING",
                    existing.generation_ids,
                    existing.started_at,
                    None,
                    now,
                    None,
                )
        except IntegrityError:
            return None

    def advance_schedule(self, profile_id: str, *, next_due_at: datetime, updated_at: datetime) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(_delivery_schedules)
                .where(_delivery_schedules.c.profile_id == profile_id)
                .values(next_due_at=_as_utc(next_due_at), updated_at=_as_utc(updated_at))
            )


class GovernedRunProvider(Protocol):
    def latest_released_run(
        self,
        profile: DeliveryProfile,
        scheduled_for: datetime,
    ) -> GovernedForecastRun | None:
        ...


class NoReleasedRunProvider:
    def latest_released_run(
        self,
        profile: DeliveryProfile,
        scheduled_for: datetime,
    ) -> GovernedForecastRun | None:
        return None


class DeliverySchedulerService:
    def __init__(
        self,
        platform_admin: PlatformAdminService,
        schedules: DeliveryScheduleRepository,
        run_provider: GovernedRunProvider,
        exports: DataExportService,
        delivery: DeliveryExecutionService,
        *,
        now_fn=None,
        waiting_retry: timedelta = timedelta(minutes=5),
        lease_timeout: timedelta = timedelta(minutes=15),
    ):
        self.platform_admin = platform_admin
        self.schedules = schedules
        self.run_provider = run_provider
        self.exports = exports
        self.delivery = delivery
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self.waiting_retry = waiting_retry
        self.lease_timeout = lease_timeout

    def _now(self) -> datetime:
        return _as_utc(self._now_fn())

    @staticmethod
    def _audit_payload(schedule: DeliverySchedule | None):
        if schedule is None:
            return None
        return {
            "profile_id": schedule.profile_id,
            "company_id": schedule.company_id,
            "timezone_name": schedule.timezone_name,
            "delivery_time": schedule.delivery_time.isoformat(),
            "first_delivery_date": schedule.first_delivery_date.isoformat(),
            "next_due_at": schedule.next_due_at.isoformat(),
            "enabled": schedule.enabled,
        }

    def save_schedule(
        self,
        profile_id: str,
        *,
        timezone_name: str,
        delivery_time: time,
        first_delivery_date: date,
        enabled: bool,
        actor: str,
        reason: str,
    ) -> DeliverySchedule:
        profile = self.platform_admin.repo.get_delivery_profile(profile_id)
        if profile is None:
            raise ConfigurationError(f"unknown delivery profile: {profile_id}")
        if profile.frequency not in {"Daily", "Weekly", "Monthly"}:
            raise ConfigurationError(f"unsupported delivery frequency: {profile.frequency}")
        _timezone(timezone_name)
        now = self._now()
        old = self.schedules.get_schedule(profile_id)
        schedule = DeliverySchedule(
            profile_id=profile.profile_id,
            company_id=profile.company_id,
            timezone_name=timezone_name,
            delivery_time=delivery_time,
            first_delivery_date=first_delivery_date,
            next_due_at=initial_occurrence(timezone_name, delivery_time, first_delivery_date),
            enabled=bool(enabled),
            updated_at=now,
        )
        self.schedules.save_schedule(schedule)
        self.platform_admin._audit(
            company_id=profile.company_id,
            entity_type="delivery_schedule",
            entity_id=profile.profile_id,
            action="UPDATE" if old else "CREATE",
            actor=actor,
            old_value=self._audit_payload(old),
            new_value=self._audit_payload(schedule),
            reason=reason,
            effective_date=first_delivery_date,
        )
        return schedule

    def run_due_once(
        self,
        *,
        actor: str = "HTC Delivery Scheduler",
        force_retry_waiting: bool = False,
    ) -> list[ScheduleExecutionRecord]:
        now = self._now()
        results: list[ScheduleExecutionRecord] = []
        for schedule in self.schedules.list_schedules():
            if not schedule.enabled or schedule.next_due_at > now:
                continue
            claimed = self.schedules.claim(
                schedule,
                now=now,
                waiting_retry=self.waiting_retry,
                lease_timeout=self.lease_timeout,
                force_retry_waiting=force_retry_waiting,
            )
            if claimed is None:
                continue
            results.append(self._execute_claim(schedule, claimed, actor=actor))
        return results

    def _execute_claim(
        self,
        schedule: DeliverySchedule,
        claimed: ScheduleExecutionRecord,
        *,
        actor: str,
    ) -> ScheduleExecutionRecord:
        now = self._now()
        profile = self.platform_admin.repo.get_delivery_profile(schedule.profile_id)
        if profile is None or profile.company_id != schedule.company_id or not profile.active:
            return self._finish(
                schedule,
                claimed,
                status="FAILED",
                generation_ids=claimed.generation_ids,
                error_message="Delivery profile is missing, cross-company, or inactive",
                now=now,
                profile=profile,
            )

        try:
            run = self.run_provider.latest_released_run(profile, claimed.scheduled_for)
        except Exception as exc:
            return self._wait_for_run(claimed, f"Released-run provider failed: {exc}")
        if run is None:
            return self._wait_for_run(claimed, "No released governed forecast run is available")

        formats = sorted({fmt.upper() for fmt in profile.formats if fmt.upper() in SUPPORTED_SCHEDULED_FORMATS})
        if not formats:
            return self._finish(
                schedule,
                claimed,
                status="FAILED",
                generation_ids=claimed.generation_ids,
                error_message="Delivery profile has no supported scheduled export format (CSV/XLSX)",
                now=now,
                profile=profile,
            )

        generation_ids = list(claimed.generation_ids)
        delivered_count = 0
        unsuccessful_count = 0
        errors: list[str] = []
        for output_format in formats:
            try:
                generated = self.exports.generate(
                    run,
                    company_id=profile.company_id,
                    output_format=output_format,
                    generated_by=actor,
                    delivery_profile_id=profile.profile_id,
                )
                generation_ids.append(generated.generation_id)
            except Exception as exc:
                unsuccessful_count += 1
                errors.append(f"{output_format} generation: {exc}")
                continue
            try:
                attempts = self.delivery.deliver(generated.generation_id, delivered_by=actor)
            except Exception as exc:
                unsuccessful_count += 1
                errors.append(f"{output_format} delivery: {exc}")
                continue
            for attempt in attempts:
                if attempt.status == "DELIVERED":
                    delivered_count += 1
                else:
                    unsuccessful_count += 1
                    if attempt.error_message:
                        errors.append(f"{output_format} {attempt.channel}: {attempt.error_message}")

        if delivered_count > 0 and unsuccessful_count == 0:
            status = "COMPLETED"
        elif delivered_count > 0:
            status = "PARTIAL"
        else:
            status = "FAILED"
        return self._finish(
            schedule,
            claimed,
            status=status,
            generation_ids=tuple(generation_ids),
            error_message="; ".join(errors)[:2000] if errors else None,
            now=self._now(),
            profile=profile,
        )

    def _wait_for_run(self, claimed: ScheduleExecutionRecord, message: str) -> ScheduleExecutionRecord:
        now = self._now()
        record = ScheduleExecutionRecord(
            claimed.execution_id,
            claimed.profile_id,
            claimed.company_id,
            claimed.scheduled_for,
            "WAITING_FOR_RUN",
            claimed.generation_ids,
            claimed.started_at,
            None,
            now,
            message[:2000],
        )
        self.schedules.update_execution(record)
        return record

    def _finish(
        self,
        schedule: DeliverySchedule,
        claimed: ScheduleExecutionRecord,
        *,
        status: str,
        generation_ids: tuple[str, ...],
        error_message: str | None,
        now: datetime,
        profile: DeliveryProfile | None,
    ) -> ScheduleExecutionRecord:
        record = ScheduleExecutionRecord(
            claimed.execution_id,
            claimed.profile_id,
            claimed.company_id,
            claimed.scheduled_for,
            status,
            tuple(generation_ids),
            claimed.started_at,
            now,
            now,
            error_message,
        )
        self.schedules.update_execution(record)
        if profile is not None and status in TERMINAL_STATUSES:
            next_due = next_future_occurrence(schedule, profile.frequency, claimed.scheduled_for, now)
            self.schedules.advance_schedule(schedule.profile_id, next_due_at=next_due, updated_at=now)
        return record

    def monitor_rows(self, company_id: str | None = None) -> list[dict]:
        now = self._now()
        rows: list[dict] = []
        for schedule in self.schedules.list_schedules(company_id):
            profile = self.platform_admin.repo.get_delivery_profile(schedule.profile_id)
            executions = self.schedules.list_executions(profile_id=schedule.profile_id)
            latest = executions[0] if executions else None
            latest_success = next((item for item in executions if item.status == "COMPLETED"), None)
            rows.append({
                "Company": schedule.company_id,
                "Profile": profile.name if profile else schedule.profile_id,
                "Frequency": profile.frequency if profile else "-",
                "Local schedule": f"{schedule.first_delivery_date.isoformat()} @ {schedule.delivery_time.strftime('%H:%M')}",
                "Timezone": schedule.timezone_name,
                "Next due": schedule.next_due_at,
                "Status": effective_schedule_status(schedule, latest, now),
                "Last attempt": latest.last_checked_at if latest else None,
                "Last successful completion": latest_success.completed_at if latest_success else None,
                "Reason": latest.error_message if latest and latest.status in {"WAITING_FOR_RUN", "FAILED", "PARTIAL"} else "",
            })
        return rows
