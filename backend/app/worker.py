import logging
import os
import signal
import threading
from pathlib import Path

import psycopg

logger = logging.getLogger("formula1_dashboard.worker")
stop_event = threading.Event()


def request_shutdown(signum: int, _frame: object) -> None:
    logger.info("Received signal %s; stopping worker scaffold.", signum)
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

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    verify_database(database_url)
    ready_file.write_text("ready\n", encoding="utf-8")
    logger.info("Worker scaffold is ready. No job processing is implemented.")

    try:
        stop_event.wait()
    finally:
        ready_file.unlink(missing_ok=True)
        logger.info("Worker scaffold stopped.")


if __name__ == "__main__":
    main()

