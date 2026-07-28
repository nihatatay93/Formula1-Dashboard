import logging
import os
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import psycopg

from app.db.schema import verify_database_schema
from app.db.session import create_session_factory
from app.ingestion.archive_ingestion import SessionFactory
from app.ingestion.backfill_worker import (
    ArchiveBackfillWorker,
    WorkerMaintenanceSummary,
    perform_worker_maintenance,
)
from app.ingestion.fastf1_loader import create_fastf1_session_loader
from app.ingestion.fastf1_schedule import (
    FastF1ScheduleLoaderProtocol,
    create_fastf1_schedule_loader,
)
from app.ingestion.request_budget import FastF1RequestBudget
from app.ingestion.runtime_policy import BackfillRuntimeSettings
from app.ingestion.season_backfill import (
    SeasonBackfillPlan,
    ensure_season_backfill,
)
from app.ingestion.telemetry_ingestion import (
    process_next_telemetry_lap,
    recover_stale_telemetry_leases,
)

logger = logging.getLogger("formula1_dashboard.worker")
stop_event = threading.Event()


def request_shutdown(signum: int, _frame: object) -> None:
    logger.info("Received signal %s; stopping backfill worker.", signum)
    stop_event.set()


def verify_database(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            verify_database_schema(cursor)


def _perform_all_maintenance(
    *,
    session_factory: SessionFactory,
    settings: BackfillRuntimeSettings,
) -> WorkerMaintenanceSummary:
    archive = perform_worker_maintenance(
        session_factory=session_factory,
        settings=settings,
    )
    with session_factory() as database:
        telemetry_count = recover_stale_telemetry_leases(
            database,
            settings=settings,
        )
    return WorkerMaintenanceSummary(
        recovered=archive.recovered,
        aggregations=archive.aggregations,
        recovered_telemetry=telemetry_count,
    )


def plan_current_season(
    *,
    session_factory: SessionFactory,
    schedule_loader: FastF1ScheduleLoaderProtocol,
    settings: BackfillRuntimeSettings,
    now_provider: Callable[[], datetime] | None = None,
) -> SeasonBackfillPlan:
    observed_at = (now_provider or (lambda: datetime.now(UTC)))()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("current-season planning time must include a timezone")
    return ensure_season_backfill(
        season_year=observed_at.astimezone(UTC).year,
        session_factory=session_factory,
        schedule_loader=schedule_loader,
        settings=settings,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    database_url = os.environ["DATABASE_URL"]
    ready_file = Path(
        os.getenv(
            "WORKER_READY_FILE",
            "/tmp/formula1-dashboard-worker.ready",
        )
    )

    stop_event.clear()
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    ready_file.unlink(missing_ok=True)
    try:
        verify_database(database_url)
        settings = BackfillRuntimeSettings.from_environment()
        session_factory = create_session_factory()
        loader = create_fastf1_session_loader(
            request_budget=FastF1RequestBudget(
                session_factory=session_factory,
                operation="archive",
                settings=settings,
            )
        )
        telemetry_loader = create_fastf1_session_loader(
            request_budget=FastF1RequestBudget(
                session_factory=session_factory,
                operation="telemetry",
                settings=settings,
            )
        )
        automatic_planner = None
        if settings.automatic_current_season_planning_enabled:
            schedule_loader = create_fastf1_schedule_loader(
                request_budget=FastF1RequestBudget(
                    session_factory=session_factory,
                    operation="schedule",
                    settings=settings,
                )
            )
            automatic_planner = partial(
                plan_current_season,
                session_factory=session_factory,
                schedule_loader=schedule_loader,
                settings=settings,
            )
        worker = ArchiveBackfillWorker(
            session_factory=session_factory,
            loader=loader,
            settings=settings,
            process_next_telemetry=lambda: process_next_telemetry_lap(
                session_factory=session_factory,
                loader=telemetry_loader,
                settings=settings,
            ),
            maintenance=lambda: _perform_all_maintenance(
                session_factory=session_factory,
                settings=settings,
            ),
            automatic_planner=automatic_planner,
        )
        ready_file.write_text("ready\n", encoding="utf-8")
    except Exception as error:
        ready_file.unlink(missing_ok=True)
        logger.error(
            "Worker initialization failed with %s; details were not logged.",
            type(error).__name__,
        )
        raise SystemExit(1) from None

    logger.info("Archive backfill worker is ready.")

    try:
        worker.run(stop_event)
    finally:
        ready_file.unlink(missing_ok=True)
        logger.info("Archive backfill worker stopped.")


if __name__ == "__main__":
    main()
