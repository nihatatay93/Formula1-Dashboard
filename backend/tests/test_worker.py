import logging
from datetime import UTC, datetime, timedelta
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


def test_worker_plans_current_season_at_startup_before_maintenance() -> None:
    stop_event = Event()
    calls: list[str] = []

    def planner() -> None:
        calls.append("planner")

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
        automatic_planner=planner,
        process_next=process_next,
        maintenance=maintenance,
    )

    worker.run(stop_event)

    assert calls == ["planner", "maintenance", "process"]


def test_disabled_automatic_planning_never_invokes_callback() -> None:
    stop_event = Event()
    planner_called = False

    def planner() -> None:
        nonlocal planner_called
        planner_called = True

    def process_next() -> None:
        stop_event.set()
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        settings=BackfillRuntimeSettings(
            automatic_current_season_planning_enabled=False,
        ),
        automatic_planner=planner,
        process_next=process_next,
        maintenance=empty_maintenance,
    )

    worker.run(stop_event)

    assert planner_called is False


def test_automatic_planning_failure_is_safe_and_nonfatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop_event = Event()
    process_called = False

    def planner() -> None:
        raise RuntimeError("RAW-PLANNER-SECRET-SENTINEL")

    def process_next() -> None:
        nonlocal process_called
        process_called = True
        stop_event.set()
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        automatic_planner=planner,
        process_next=process_next,
        maintenance=empty_maintenance,
    )

    with caplog.at_level(
        logging.ERROR,
        logger="formula1_dashboard.worker",
    ):
        worker.run(stop_event)

    assert process_called is True
    assert "RuntimeError" in caplog.text
    assert "RAW-PLANNER-SECRET-SENTINEL" not in caplog.text


def test_current_season_planner_uses_utc_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_factory = object()
    sentinel_loader = object()
    sentinel_settings = BackfillRuntimeSettings()
    sentinel_plan = object()
    calls: list[dict[str, object]] = []

    def ensure(**kwargs):
        calls.append(kwargs)
        return sentinel_plan

    monkeypatch.setattr(worker_entrypoint, "ensure_season_backfill", ensure)

    result = worker_entrypoint.plan_current_season(
        session_factory=sentinel_factory,  # type: ignore[arg-type]
        schedule_loader=sentinel_loader,  # type: ignore[arg-type]
        settings=sentinel_settings,
        now_provider=lambda: datetime(
            2027,
            1,
            1,
            0,
            30,
            tzinfo=UTC,
        ),
    )

    assert result is sentinel_plan
    assert calls == [
        {
            "season_year": 2027,
            "session_factory": sentinel_factory,
            "schedule_loader": sentinel_loader,
            "settings": sentinel_settings,
        }
    ]


def test_automatic_planner_runs_again_at_configured_interval() -> None:
    clock = [0.0]
    planner_calls = 0

    class ControlledStopEvent:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def set(self) -> None:
            self.stopped = True

        def wait(self, seconds: float) -> bool:
            clock[0] += seconds
            return self.stopped

    stop = ControlledStopEvent()

    def planner() -> None:
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 2:
            stop.set()

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        settings=BackfillRuntimeSettings(
            automatic_current_season_planning_interval_seconds=60,
        ),
        monotonic=lambda: clock[0],
        automatic_planner=planner,
        process_next=lambda: None,
        maintenance=empty_maintenance,
    )

    worker.run(stop)  # type: ignore[arg-type]

    assert planner_calls == 2
    assert clock[0] == 60


def test_shutdown_during_automatic_planning_stops_new_work() -> None:
    stop_event = Event()
    maintenance_called = False
    process_called = False

    def planner() -> None:
        stop_event.set()

    def maintenance() -> WorkerMaintenanceSummary:
        nonlocal maintenance_called
        maintenance_called = True
        return empty_maintenance()

    def process_next() -> None:
        nonlocal process_called
        process_called = True
        return None

    worker = ArchiveBackfillWorker(
        session_factory=unused_session_factory,  # type: ignore[arg-type]
        loader=UnusedLoader(),
        automatic_planner=planner,
        process_next=process_next,
        maintenance=maintenance,
    )

    worker.run(stop_event)

    assert maintenance_called is False
    assert process_called is False


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
