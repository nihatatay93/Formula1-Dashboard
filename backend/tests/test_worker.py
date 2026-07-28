import logging
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import app.worker as worker_entrypoint
from app.ingestion.backfill_worker import (
    ArchiveBackfillWorker,
    ClaimHeartbeatMonitor,
    WorkerMaintenanceSummary,
)
from app.ingestion.fastf1_loader import (
    FastF1SessionRequest,
    LoadedFastF1Session,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings


class UnusedLoader:
    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        raise AssertionError(f"loader must not be called: {request!r}")


def unused_session_factory() -> object:
    raise AssertionError("session factory must not be called")


def empty_maintenance() -> WorkerMaintenanceSummary:
    return WorkerMaintenanceSummary(recovered=(), aggregations=())


def test_heartbeat_monitor_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        ClaimHeartbeatMonitor(
            session_factory=unused_session_factory,  # type: ignore[arg-type]
            claim=object(),  # type: ignore[arg-type]
            interval=timedelta(0),
        )


def test_worker_runs_maintenance_before_its_first_claim() -> None:
    stop_event = Event()
    calls: list[str] = []

    def maintenance() -> WorkerMaintenanceSummary:
        calls.append("maintenance")
        return empty_maintenance()

    def process_next() -> None:
        calls.append("process")
        stop_event.set()
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        process_next=process_next,
        maintenance=maintenance,
    )

    worker.run(stop_event)

    assert calls == ["maintenance", "process"]


def test_shutdown_during_maintenance_prevents_a_new_claim() -> None:
    stop_event = Event()
    process_called = False

    def maintenance() -> WorkerMaintenanceSummary:
        stop_event.set()
        return empty_maintenance()

    def process_next() -> None:
        nonlocal process_called
        process_called = True
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        process_next=process_next,
        maintenance=maintenance,
    )

    worker.run(stop_event)

    assert process_called is False


def test_worker_prioritizes_archive_before_telemetry() -> None:
    stop_event = Event()
    calls: list[str] = []

    def process_archive():
        calls.append("archive")
        stop_event.set()
        return SimpleNamespace(
            claim=SimpleNamespace(job_id="job", session_id=1),
            outcome="completed",
        )

    def process_telemetry():
        calls.append("telemetry")
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        process_next=process_archive,
        process_next_telemetry=process_telemetry,
        maintenance=empty_maintenance,
    )

    worker.run(stop_event)

    assert calls == ["archive"]


def test_worker_processes_telemetry_when_archive_is_not_claimable() -> None:
    stop_event = Event()
    calls: list[str] = []

    def process_archive():
        calls.append("archive")
        return None

    def process_telemetry():
        calls.append("telemetry")
        stop_event.set()
        return SimpleNamespace(
            claim=SimpleNamespace(lap_id=7),
            status="completed",
        )

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        process_next=process_archive,
        process_next_telemetry=process_telemetry,
        maintenance=empty_maintenance,
    )

    worker.run(stop_event)

    assert calls == ["archive", "telemetry"]


def test_shutdown_stops_new_claims_but_waits_for_active_processing() -> None:
    stop_event = Event()
    processing_started = Event()
    release_processing = Event()
    process_calls = 0

    def process_next() -> None:
        nonlocal process_calls
        process_calls += 1
        processing_started.set()
        assert release_processing.wait(timeout=2)
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        settings=BackfillRuntimeSettings(
            worker_poll_interval_seconds=1,
        ),
        process_next=process_next,
        maintenance=empty_maintenance,
    )
    thread = Thread(target=worker.run, args=(stop_event,))
    thread.start()
    assert processing_started.wait(timeout=1)

    stop_event.set()
    thread.join(timeout=0.05)
    assert thread.is_alive()

    release_processing.set()
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert process_calls == 1


def test_worker_logs_only_exception_type_not_raw_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop_event = Event()

    def process_next() -> None:
        stop_event.set()
        raise RuntimeError("RAW-WORKER-SECRET-SENTINEL")

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        process_next=process_next,
        maintenance=empty_maintenance,
    )

    with caplog.at_level(
        logging.ERROR,
        logger="formula1_dashboard.worker",
    ):
        worker.run(stop_event)

    assert "RuntimeError" in caplog.text
    assert "RAW-WORKER-SECRET-SENTINEL" not in caplog.text


def test_worker_initialization_failure_removes_stale_readiness_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_file = tmp_path / "worker.ready"
    ready_file.write_text("stale\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://controlled")
    monkeypatch.setenv("WORKER_READY_FILE", str(ready_file))
    monkeypatch.setattr(
        worker_entrypoint.signal,
        "signal",
        lambda *_args: None,
    )

    def fail_verification(_database_url: str) -> None:
        raise RuntimeError("RAW-INITIALIZATION-SECRET-SENTINEL")

    monkeypatch.setattr(
        worker_entrypoint,
        "verify_database",
        fail_verification,
    )

    with caplog.at_level(
        logging.ERROR,
        logger="formula1_dashboard.worker",
    ):
        with pytest.raises(SystemExit) as raised:
            worker_entrypoint.main()

    assert raised.value.code == 1
    assert ready_file.exists() is False
    assert "RuntimeError" in caplog.text
    assert "RAW-INITIALIZATION-SECRET-SENTINEL" not in caplog.text
