import logging
import os
import signal
import threading
from pathlib import Path

import psycopg

from app.db.session import create_session_factory
from app.ingestion.backfill_worker import ArchiveBackfillWorker
from app.ingestion.fastf1_loader import create_fastf1_session_loader
from app.ingestion.runtime_policy import BackfillRuntimeSettings

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
        loader = create_fastf1_session_loader()
        worker = ArchiveBackfillWorker(
            session_factory=create_session_factory(),
            loader=loader,
            settings=settings,
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
