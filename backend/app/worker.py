import logging
import os
import signal
import threading
from pathlib import Path

import psycopg

from app.db.session import create_session_factory
from app.ingestion.archive_ingestion import SessionFactory
from app.ingestion.backfill_worker import (
    ArchiveBackfillWorker,
    WorkerMaintenanceSummary,
    perform_worker_maintenance,
)
from app.ingestion.fastf1_loader import create_fastf1_session_loader
from app.ingestion.request_budget import FastF1RequestBudget
from app.ingestion.runtime_policy import BackfillRuntimeSettings
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
            cursor.execute("SELECT 1")
            cursor.fetchone()


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
