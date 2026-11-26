from __future__ import annotations

import os
from pathlib import Path
import sys
import time as time_module



from raw_material_forecasting import mail, secrets_file
from raw_material_forecasting.platform_admin.repository import PlatformAdminRepository
from raw_material_forecasting.platform_admin.service import PlatformAdminService
from raw_material_forecasting.platform_store import platform_store_engine
from raw_material_forecasting.reporting_delivery import (
    DataExportService,
    DeliveryAttemptRepository,
    DeliveryExecutionService,
    LocalSecureLocationAdapter,
    OutputGenerationRepository,
)
from raw_material_forecasting.scheduled_delivery import (
    DeliveryScheduleRepository,
    DeliverySchedulerService,
    NoReleasedRunProvider,
)


def poll_interval_seconds() -> float:
    raw = os.environ.get("HTC_DELIVERY_SCHEDULER_INTERVAL_SECONDS", "60").strip()
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return value if value > 0 else 60.0


def build_scheduler() -> DeliverySchedulerService:
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    # The mail settings are keys, and keys live in secrets.env beside the store. The application
    # reads that file when it starts; this is a process of its own and has to read it too, or the
    # worker would be the one place email was never set up (v1.2.135). Names only are returned.
    secrets_file.load()
    engine = platform_store_engine()   # v0.5.42: the same store the admin page writes - DATABASE_URL
    platform_admin = PlatformAdminService(PlatformAdminRepository(engine))
    schedules = DeliveryScheduleRepository(engine)
    generations = OutputGenerationRepository(engine)
    delivery_attempts = DeliveryAttemptRepository(engine)
    exports = DataExportService(platform_admin, generations, data_dir / "generated_outputs")
    delivery = DeliveryExecutionService(
        platform_admin,
        generations,
        delivery_attempts,
        email_adapter=mail.delivery_adapter(platform_admin.repo, sent_by="Delivery worker"),
        secure_location_adapter=LocalSecureLocationAdapter(
            Path(os.environ.get("HTC_SECURE_DELIVERY_ROOT", "data/secure_delivery"))
        ),
    )
    return DeliverySchedulerService(
        platform_admin,
        schedules,
        NoReleasedRunProvider(),
        exports,
        delivery,
    )


def run_worker(
    *,
    scheduler: DeliverySchedulerService | None = None,
    interval_seconds: float | None = None,
    sleep_fn=time_module.sleep,
    max_cycles: int | None = None,
) -> None:
    active_scheduler = scheduler or build_scheduler()
    interval = poll_interval_seconds() if interval_seconds is None else float(interval_seconds)
    if interval <= 0:
        interval = 60.0
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            results = active_scheduler.run_due_once(actor="HTC Delivery Scheduler")
            if results:
                summary = ", ".join(f"{item.profile_id}:{item.status}" for item in results)
                print(f"delivery scheduler cycle: {summary}", flush=True)
        except Exception as exc:
            print(f"delivery scheduler cycle failed: {exc}", file=sys.stderr, flush=True)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        sleep_fn(interval)


if __name__ == "__main__":
    run_worker()
