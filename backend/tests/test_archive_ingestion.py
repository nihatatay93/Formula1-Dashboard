import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    Lap,
    SessionEntry,
    SessionIngestion,
    SessionResult,
)
from app.ingestion.archive_attempt import (
    ArchiveIngestionAlreadyRunningError,
    ArchiveIngestionStateError,
    mark_archive_ingestion_pending,
    run_fastf1_archive_ingestion_attempt,
)
from app.ingestion.archive_ingestion import (
    ArchiveSessionIdentityError,
    ingest_fastf1_archive_session,
)
from app.ingestion.archive_persistence import (
    ArchivePersistenceTargetChangedError,
    ArchiveSessionNotFoundError,
    ArchiveSourceConflictError,
)
from app.ingestion.backfill_orchestration import (
    claim_next_archive_job_session,
    recover_stale_archive_job_sessions,
)
from app.ingestion.backfill_worker import (
    WorkerSessionOutcome,
    perform_worker_maintenance,
    process_next_archive_job_session,
)
from app.ingestion.fastf1_loader import (
    FastF1SessionLoadError,
    FastF1SessionRequest,
    LoadedFastF1Session,
)
from app.ingestion.fastf1_normalization import FastF1NormalizationError

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for archive ingestion tests",
)


@dataclass(frozen=True)
class IngestionTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    session_id: int
    event_id: int
    season_year: int
    driver_id: str


class StubLoader:
    def __init__(
        self,
        *,
        results: pd.DataFrame,
        laps: pd.DataFrame,
        session_name: str = "Race",
    ) -> None:
        self.results = results
        self.laps = laps
        self.session_name = session_name
        self.requests: list[FastF1SessionRequest] = []

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        self.requests.append(request)
        return LoadedFastF1Session(
            request=request,
            session_name=self.session_name,
            results=self.results,
            laps=self.laps,
        )


class FailingLoader:
    def __init__(self) -> None:
        self.requests: list[FastF1SessionRequest] = []

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        self.requests.append(request)
        raise FastF1SessionLoadError("controlled loader failure")


class TargetChangingLoader(StubLoader):
    def __init__(
        self,
        *,
        target: IngestionTarget,
        results: pd.DataFrame,
        laps: pd.DataFrame,
    ) -> None:
        super().__init__(results=results, laps=laps)
        self.target = target

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        loaded = super().load(request)
        with self.target.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE sessions
                    SET session_name = 'Qualifying'
                    WHERE id = :session_id
                    """
                ),
                {"session_id": self.target.session_id},
            )
        return loaded


class StateInspectingLoader(StubLoader):
    def __init__(
        self,
        *,
        target: IngestionTarget,
        results: pd.DataFrame,
        laps: pd.DataFrame,
        expected_started_at: datetime,
    ) -> None:
        super().__init__(results=results, laps=laps)
        self.target = target
        self.expected_started_at = expected_started_at

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        with Session(self.target.engine) as database:
            ingestion = database.get(
                SessionIngestion,
                self.target.session_id,
            )
            assert ingestion is not None
            assert ingestion.status == "running"
            assert ingestion.attempt_count == 1
            assert ingestion.first_started_at == self.expected_started_at
            assert ingestion.last_started_at == self.expected_started_at
            assert ingestion.last_error_code is None
            assert ingestion.last_error_message is None
        return super().load(request)


class HeartbeatInspectingLoader(StubLoader):
    def __init__(
        self,
        *,
        target: IngestionTarget,
        job_id: uuid.UUID,
        results: pd.DataFrame,
        laps: pd.DataFrame,
    ) -> None:
        super().__init__(results=results, laps=laps)
        self.target = target
        self.job_id = job_id
        self.observed_heartbeat_advance = False

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        with self.target.session_factory() as database:
            job_session = database.get(
                BackfillJobSession,
                (self.job_id, self.target.session_id),
            )
            assert job_session is not None
            initial_heartbeat = job_session.heartbeat_at

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            time.sleep(0.01)
            with self.target.session_factory() as database:
                job_session = database.get(
                    BackfillJobSession,
                    (self.job_id, self.target.session_id),
                )
                assert job_session is not None
                if job_session.heartbeat_at != initial_heartbeat:
                    self.observed_heartbeat_advance = True
                    break

        assert self.observed_heartbeat_advance
        return super().load(request)


class LeaseRecoveringLoader(StubLoader):
    def __init__(
        self,
        *,
        target: IngestionTarget,
        job_id: uuid.UUID,
        results: pd.DataFrame,
        laps: pd.DataFrame,
    ) -> None:
        super().__init__(results=results, laps=laps)
        self.target = target
        self.job_id = job_id

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        with self.target.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE backfill_job_sessions
                    SET heartbeat_at = clock_timestamp() - interval '10 minutes'
                    WHERE job_id = :job_id
                      AND session_id = :session_id
                    """
                ),
                {
                    "job_id": self.job_id,
                    "session_id": self.target.session_id,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE session_ingestions
                    SET heartbeat_at = clock_timestamp() - interval '10 minutes'
                    WHERE session_id = :session_id
                    """
                ),
                {"session_id": self.target.session_id},
            )
        with self.target.session_factory() as database:
            recovered = recover_stale_archive_job_sessions(
                database,
                jitter_fraction_factory=lambda: 0.5,
            )
        assert len(recovered) == 1
        return super().load(request)


class SecretFailingLoader:
    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        raise FastF1SessionLoadError(
            "sensitive=RAW-ERROR-SENTINEL; "
            f"request={request!r}"
        )


@pytest.fixture
def ingestion_target() -> Any:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    token = uuid.uuid4().hex
    driver_id = f"vertical_{token}"

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(32000, 33999) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM seasons
                    WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            )
        )
        assert season_year is not None
        connection.execute(
            text("INSERT INTO seasons (year) VALUES (:year)"),
            {"year": season_year},
        )
        event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year,
                    round_number,
                    event_name,
                    source
                )
                VALUES (
                    :season_year,
                    7,
                    :event_name,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "event_name": f"Vertical Slice Test {token}",
            },
        )
        assert event_id is not None
        session_id = connection.scalar(
            text(
                """
                INSERT INTO sessions (
                    event_id,
                    session_key,
                    session_name,
                    source
                )
                VALUES (
                    :event_id,
                    'race',
                    'Race',
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {"event_id": event_id},
        )
        assert session_id is not None

    target = IngestionTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        session_id=session_id,
        event_id=event_id,
        season_year=season_year,
        driver_id=driver_id,
    )

    try:
        yield target
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM laps
                    WHERE session_entry_id IN (
                        SELECT id
                        FROM session_entries
                        WHERE session_id = :session_id
                    )
                    """
                ),
                {"session_id": session_id},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM backfill_jobs
                    WHERE season_year = :season_year
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_results
                    WHERE session_entry_id IN (
                        SELECT id
                        FROM session_entries
                        WHERE session_id = :session_id
                    )
                    """
                ),
                {"session_id": session_id},
            )
            connection.execute(
                text(
                    "DELETE FROM session_ingestions WHERE session_id = :session_id"
                ),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM session_entries WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM sessions WHERE id = :session_id"),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM events WHERE id = :event_id"),
                {"event_id": event_id},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :season_year"),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM drivers
                    WHERE jolpica_driver_id = :driver_id
                      AND NOT EXISTS (
                          SELECT 1
                          FROM session_entries
                          WHERE session_entries.driver_id = drivers.id
                      )
                    """
                ),
                {"driver_id": driver_id},
            )
        engine.dispose()


def archive_tables(target: IngestionTarget) -> tuple[pd.DataFrame, pd.DataFrame]:
    results = pd.DataFrame(
        [
            {
                "DriverNumber": "44",
                "BroadcastName": "T DRIVER",
                "Abbreviation": "TST",
                "DriverId": target.driver_id,
                "TeamName": "Test Team",
                "TeamColor": "112233",
                "TeamId": "test_team",
                "FirstName": "Test",
                "LastName": "Driver",
                "FullName": "Test Driver",
                "CountryCode": "GBR",
                "Position": 1.0,
                "ClassifiedPosition": "1",
                "GridPosition": 1.0,
                "Time": timedelta(hours=1),
                "Status": "Finished",
                "Points": 25.0,
                "Laps": 1.0,
            }
        ]
    )
    laps = pd.DataFrame(
        [
            {
                "DriverNumber": "44",
                "Driver": "TST",
                "LapNumber": 1.0,
                "LapTime": timedelta(seconds=90),
                "IsPersonalBest": True,
                "FastF1Generated": False,
                "IsAccurate": True,
            }
        ]
    )
    return results, laps


def queue_backfill_job(
    target: IngestionTarget,
    *,
    attempt_count: int = 0,
) -> uuid.UUID:
    job_id = uuid.uuid4()
    with target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason
                )
                VALUES (
                    :job_id,
                    :season_year,
                    'pending',
                    'missing'
                )
                """
            ),
            {
                "job_id": job_id,
                "season_year": target.season_year,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO backfill_job_sessions (
                    job_id,
                    session_id,
                    attempt_count
                )
                VALUES (
                    :job_id,
                    :session_id,
                    :attempt_count
                )
                """
            ),
            {
                "job_id": job_id,
                "session_id": target.session_id,
                "attempt_count": attempt_count,
            },
        )
    return job_id


def test_composes_loading_normalization_and_persistence_idempotently(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(results=results, laps=laps)
    first_completed_at = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    second_completed_at = datetime(2026, 7, 28, 15, 5, tzinfo=UTC)

    first = ingest_fastf1_archive_session(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
        loader=loader,
        completed_at=first_completed_at,
    )
    with Session(ingestion_target.engine) as database:
        first_entry_id = database.scalar(
            select(SessionEntry.id).where(
                SessionEntry.session_id == ingestion_target.session_id
            )
        )
        first_lap_id = database.scalar(
            select(Lap.id)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .where(SessionEntry.session_id == ingestion_target.session_id)
        )

    second = ingest_fastf1_archive_session(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
        loader=loader,
        completed_at=second_completed_at,
    )

    assert loader.requests == [
        FastF1SessionRequest(ingestion_target.season_year, 7, "Race"),
        FastF1SessionRequest(ingestion_target.season_year, 7, "Race"),
    ]
    assert first.loaded_session_name == "Race"
    assert first.persistence.entries_upserted == 1
    assert first.persistence.results_upserted == 1
    assert first.persistence.laps_upserted == 1
    assert second.persistence.stale_entries_deleted == 0
    assert second.persistence.stale_results_deleted == 0
    assert second.persistence.stale_laps_deleted == 0

    with Session(ingestion_target.engine) as database:
        entry = database.scalar(
            select(SessionEntry).where(
                SessionEntry.session_id == ingestion_target.session_id
            )
        )
        assert entry is not None
        lap = database.scalar(
            select(Lap).where(Lap.session_entry_id == entry.id)
        )
        assert lap is not None
        assert entry.id == first_entry_id
        assert lap.id == first_lap_id
        assert database.scalar(
            select(func.count(SessionResult.session_entry_id))
            .join(SessionEntry, SessionEntry.id == SessionResult.session_entry_id)
            .where(SessionEntry.session_id == ingestion_target.session_id)
        ) == 1
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "completed"
        assert ingestion.completed_at == second_completed_at


def test_managed_attempt_exposes_running_and_completes_atomically(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    started_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    completed_at = datetime(2026, 7, 28, 16, 5, tzinfo=UTC)
    loader = StateInspectingLoader(
        target=ingestion_target,
        results=results,
        laps=laps,
        expected_started_at=started_at,
    )

    pending = mark_archive_ingestion_pending(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
    )
    repeated_pending = mark_archive_ingestion_pending(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
    )
    summary = run_fastf1_archive_ingestion_attempt(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
        loader=loader,
        started_at=started_at,
        completed_at=completed_at,
    )

    assert pending.status == "pending"
    assert pending.attempt_count == 0
    assert repeated_pending.attempt_count == 0
    assert summary.attempt_count == 1
    assert summary.ingestion.persistence.session_id == ingestion_target.session_id

    with Session(ingestion_target.engine) as database:
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "completed"
        assert ingestion.source == "fastf1_archive"
        assert ingestion.record_state == "finalized"
        assert ingestion.attempt_count == 1
        assert ingestion.first_started_at == started_at
        assert ingestion.last_started_at == started_at
        assert ingestion.completed_at == completed_at
        assert ingestion.last_error_code is None
        assert ingestion.last_error_message is None


def test_claimed_vertical_slice_completes_the_owned_job_session(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = uuid.uuid4()
    with ingestion_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason
                )
                VALUES (
                    :job_id,
                    :season_year,
                    'pending',
                    'missing'
                )
                """
            ),
            {
                "job_id": job_id,
                "season_year": ingestion_target.season_year,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO backfill_job_sessions (
                    job_id,
                    session_id
                )
                VALUES (
                    :job_id,
                    :session_id
                )
                """
            ),
            {
                "job_id": job_id,
                "session_id": ingestion_target.session_id,
            },
        )

    with ingestion_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None

    results, laps = archive_tables(ingestion_target)
    completed_at = datetime(2026, 7, 28, 16, 30, tzinfo=UTC)
    summary = ingest_fastf1_archive_session(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
        loader=StubLoader(results=results, laps=laps),
        completed_at=completed_at,
        claim=claim,
    )

    assert summary.persistence.session_id == ingestion_target.session_id
    with ingestion_target.session_factory() as database:
        job_session = database.get(
            BackfillJobSession,
            (job_id, ingestion_target.session_id),
        )
        ingestion = database.get(
            SessionIngestion,
            ingestion_target.session_id,
        )

        assert job_session is not None
        assert job_session.status == "completed"
        assert job_session.completed_at == completed_at
        assert ingestion is not None
        assert ingestion.status == "completed"
        assert ingestion.completed_at == completed_at


def test_managed_failure_records_only_fixed_sanitized_diagnostics(
    ingestion_target: IngestionTarget,
) -> None:
    started_at = datetime(2026, 7, 28, 17, 0, tzinfo=UTC)

    with pytest.raises(FastF1SessionLoadError, match="RAW-ERROR-SENTINEL"):
        run_fastf1_archive_ingestion_attempt(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=SecretFailingLoader(),
            started_at=started_at,
        )

    with Session(ingestion_target.engine) as database:
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "failed"
        assert ingestion.attempt_count == 1
        assert ingestion.first_started_at == started_at
        assert ingestion.last_started_at == started_at
        assert ingestion.completed_at is None
        assert ingestion.last_error_code == "fastf1_load_failed"
        assert ingestion.last_error_message == "FastF1 session loading failed."
        assert "RAW-ERROR-SENTINEL" not in ingestion.last_error_message
        assert "sensitive=" not in ingestion.last_error_message

    pending = mark_archive_ingestion_pending(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
    )
    assert pending.status == "pending"
    assert pending.attempt_count == 1
    assert pending.last_error_code == "fastf1_load_failed"
    assert pending.last_error_message == "FastF1 session loading failed."


def test_failed_reingestion_preserves_previous_completed_snapshot(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    first_started_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    completed_at = datetime(2026, 7, 28, 18, 5, tzinfo=UTC)
    source_updated_at = datetime(2026, 7, 28, 17, 30, tzinfo=UTC)
    run_fastf1_archive_ingestion_attempt(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
        loader=StubLoader(results=results, laps=laps),
        started_at=first_started_at,
        completed_at=completed_at,
        source_updated_at=source_updated_at,
    )
    pending = mark_archive_ingestion_pending(
        session_id=ingestion_target.session_id,
        session_factory=ingestion_target.session_factory,
    )

    assert pending.completed_at == completed_at
    with pytest.raises(FastF1SessionLoadError):
        run_fastf1_archive_ingestion_attempt(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=SecretFailingLoader(),
            started_at=datetime(2026, 7, 28, 19, 0, tzinfo=UTC),
        )

    with Session(ingestion_target.engine) as database:
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "failed"
        assert ingestion.attempt_count == 2
        assert ingestion.first_started_at == first_started_at
        assert ingestion.completed_at == completed_at
        assert ingestion.source_updated_at == source_updated_at
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == ingestion_target.session_id
            )
        ) == 1


def test_managed_attempt_rejects_an_existing_running_state(
    ingestion_target: IngestionTarget,
) -> None:
    with ingestion_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count
                )
                VALUES (
                    :session_id,
                    'running',
                    'fastf1_archive',
                    'finalized',
                    3
                )
                """
            ),
            {"session_id": ingestion_target.session_id},
        )
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(results=results, laps=laps)

    with pytest.raises(ArchiveIngestionAlreadyRunningError, match="already running"):
        run_fastf1_archive_ingestion_attempt(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert loader.requests == []
    with Session(ingestion_target.engine) as database:
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.attempt_count == 3


def test_managed_attempt_preserves_non_archive_ingestion_state(
    ingestion_target: IngestionTarget,
) -> None:
    with ingestion_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state
                )
                VALUES (
                    :session_id,
                    'running',
                    'live_signalr',
                    'provisional'
                )
                """
            ),
            {"session_id": ingestion_target.session_id},
        )
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(results=results, laps=laps)

    with pytest.raises(ArchiveSourceConflictError, match="another source"):
        run_fastf1_archive_ingestion_attempt(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert loader.requests == []
    with Session(ingestion_target.engine) as database:
        ingestion = database.get(SessionIngestion, ingestion_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.source == "live_signalr"
        assert ingestion.record_state == "provisional"


def test_managed_attempt_rejects_naive_started_at_without_creating_state(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(results=results, laps=laps)

    with pytest.raises(ArchiveIngestionStateError, match="timezone"):
        run_fastf1_archive_ingestion_attempt(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
            started_at=datetime(2026, 7, 28, 20, 0),
        )

    assert loader.requests == []
    assert_session_has_no_archive_state(ingestion_target)


def test_loader_failure_does_not_change_database(
    ingestion_target: IngestionTarget,
) -> None:
    loader = FailingLoader()

    with pytest.raises(FastF1SessionLoadError, match="controlled"):
        ingest_fastf1_archive_session(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert loader.requests == [
        FastF1SessionRequest(ingestion_target.season_year, 7, "Race")
    ]
    assert_session_has_no_archive_state(ingestion_target)


def test_normalization_failure_does_not_change_database(
    ingestion_target: IngestionTarget,
) -> None:
    loader = StubLoader(results=pd.DataFrame(), laps=pd.DataFrame())

    with pytest.raises(FastF1NormalizationError, match="at least one"):
        ingest_fastf1_archive_session(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert_session_has_no_archive_state(ingestion_target)


def test_loaded_session_name_mismatch_does_not_change_database(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(
        results=results,
        laps=laps,
        session_name="Qualifying",
    )

    with pytest.raises(ArchiveSessionIdentityError, match="expects 'Race'"):
        ingest_fastf1_archive_session(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert_session_has_no_archive_state(ingestion_target)


def test_target_identity_change_before_persistence_is_rejected(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    loader = TargetChangingLoader(
        target=ingestion_target,
        results=results,
        laps=laps,
    )

    with pytest.raises(ArchivePersistenceTargetChangedError, match="changed"):
        ingest_fastf1_archive_session(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert_session_has_no_archive_state(ingestion_target)


def test_missing_database_session_does_not_call_loader(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    loader = StubLoader(results=results, laps=laps)

    with pytest.raises(ArchiveSessionNotFoundError, match="does not exist"):
        ingest_fastf1_archive_session(
            session_id=9_223_372_036_854_775_000,
            session_factory=ingestion_target.session_factory,
            loader=loader,
        )

    assert loader.requests == []


def test_pre_persistence_guard_aborts_before_any_database_write(
    ingestion_target: IngestionTarget,
) -> None:
    results, laps = archive_tables(ingestion_target)
    guard_calls = 0

    def reject_persistence() -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise RuntimeError("controlled guard rejection")

    with pytest.raises(RuntimeError, match="guard rejection"):
        ingest_fastf1_archive_session(
            session_id=ingestion_target.session_id,
            session_factory=ingestion_target.session_factory,
            loader=StubLoader(results=results, laps=laps),
            before_persist=reject_persistence,
        )

    assert guard_calls == 1
    assert_session_has_no_archive_state(ingestion_target)


def test_worker_processes_one_session_with_periodic_heartbeats(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = queue_backfill_job(ingestion_target)
    results, laps = archive_tables(ingestion_target)
    loader = HeartbeatInspectingLoader(
        target=ingestion_target,
        job_id=job_id,
        results=results,
        laps=laps,
    )

    processed = process_next_archive_job_session(
        session_factory=ingestion_target.session_factory,
        loader=loader,
        heartbeat_interval=timedelta(milliseconds=10),
    )

    assert processed is not None
    assert processed.claim.job_id == job_id
    assert processed.outcome is WorkerSessionOutcome.COMPLETED
    assert processed.ingestion is not None
    assert processed.failure_transition is None
    assert processed.aggregation.status == "completed"
    assert processed.aggregation.completed_sessions == 1
    assert loader.observed_heartbeat_advance is True
    with ingestion_target.session_factory() as database:
        job = database.get(BackfillJob, job_id)
        job_session = database.get(
            BackfillJobSession,
            (job_id, ingestion_target.session_id),
        )
        ingestion = database.get(
            SessionIngestion,
            ingestion_target.session_id,
        )
        assert job is not None
        assert job.status == "completed"
        assert job_session is not None
        assert job_session.status == "completed"
        assert ingestion is not None
        assert ingestion.status == "completed"


def test_worker_records_retryable_loader_failure_and_keeps_job_running(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = queue_backfill_job(ingestion_target)

    processed = process_next_archive_job_session(
        session_factory=ingestion_target.session_factory,
        loader=FailingLoader(),
        jitter_fraction_factory=lambda: 0.5,
    )

    assert processed is not None
    assert processed.outcome is WorkerSessionOutcome.RETRY_PENDING
    assert processed.ingestion is None
    assert processed.failure_transition is not None
    assert processed.failure_transition.status == "pending"
    assert processed.aggregation.status == "running"
    assert processed.aggregation.pending_sessions == 1
    with ingestion_target.session_factory() as database:
        job = database.get(BackfillJob, job_id)
        job_session = database.get(
            BackfillJobSession,
            (job_id, ingestion_target.session_id),
        )
        ingestion = database.get(
            SessionIngestion,
            ingestion_target.session_id,
        )
        assert job is not None
        assert job.status == "running"
        assert job_session is not None
        assert job_session.status == "pending"
        assert job_session.next_retry_at is not None
        assert ingestion is not None
        assert ingestion.status == "pending"
        assert ingestion.next_retry_at == job_session.next_retry_at


def test_worker_does_not_persist_after_its_lease_is_recovered(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = queue_backfill_job(ingestion_target)
    results, laps = archive_tables(ingestion_target)

    processed = process_next_archive_job_session(
        session_factory=ingestion_target.session_factory,
        loader=LeaseRecoveringLoader(
            target=ingestion_target,
            job_id=job_id,
            results=results,
            laps=laps,
        ),
        heartbeat_interval=timedelta(hours=1),
    )

    assert processed is not None
    assert processed.outcome is WorkerSessionOutcome.OWNERSHIP_LOST
    assert processed.ingestion is None
    assert processed.failure_transition is None
    assert processed.aggregation.status == "running"
    assert processed.aggregation.pending_sessions == 1
    with ingestion_target.session_factory() as database:
        job_session = database.get(
            BackfillJobSession,
            (job_id, ingestion_target.session_id),
        )
        ingestion = database.get(
            SessionIngestion,
            ingestion_target.session_id,
        )
        assert job_session is not None
        assert job_session.status == "pending"
        assert job_session.last_error_code == "worker_lease_expired"
        assert ingestion is not None
        assert ingestion.status == "pending"
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == ingestion_target.session_id
            )
        ) == 0


def test_worker_records_terminal_normalization_failure_and_fails_parent(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = queue_backfill_job(ingestion_target)
    jitter_requested = False

    def unexpected_jitter() -> float:
        nonlocal jitter_requested
        jitter_requested = True
        return 0.5

    processed = process_next_archive_job_session(
        session_factory=ingestion_target.session_factory,
        loader=StubLoader(
            results=pd.DataFrame(),
            laps=pd.DataFrame(),
        ),
        jitter_fraction_factory=unexpected_jitter,
    )

    assert processed is not None
    assert processed.outcome is WorkerSessionOutcome.FAILED
    assert processed.failure_transition is not None
    assert processed.failure_transition.status == "failed"
    assert processed.aggregation.status == "failed"
    assert processed.aggregation.failed_sessions == 1
    assert jitter_requested is False
    with ingestion_target.session_factory() as database:
        job = database.get(BackfillJob, job_id)
        job_session = database.get(
            BackfillJobSession,
            (job_id, ingestion_target.session_id),
        )
        ingestion = database.get(
            SessionIngestion,
            ingestion_target.session_id,
        )
        assert job is not None
        assert job.status == "failed"
        assert job.last_error_code == "session_ingestion_failed"
        assert job.last_error_message == (
            "One or more session ingestions failed."
        )
        assert job_session is not None
        assert job_session.status == "failed"
        assert job_session.last_error_code == "fastf1_normalization_failed"
        assert ingestion is not None
        assert ingestion.status == "failed"


def test_worker_maintenance_recovers_and_aggregates_terminal_lease(
    ingestion_target: IngestionTarget,
) -> None:
    job_id = queue_backfill_job(
        ingestion_target,
        attempt_count=3,
    )
    with ingestion_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None
    assert claim.job_attempt_count == 4
    with ingestion_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE backfill_job_sessions
                SET heartbeat_at = clock_timestamp() - interval '10 minutes'
                WHERE job_id = :job_id
                  AND session_id = :session_id
                """
            ),
            {
                "job_id": job_id,
                "session_id": ingestion_target.session_id,
            },
        )
        connection.execute(
            text(
                """
                UPDATE session_ingestions
                SET heartbeat_at = clock_timestamp() - interval '10 minutes'
                WHERE session_id = :session_id
                """
            ),
            {"session_id": ingestion_target.session_id},
        )

    maintenance = perform_worker_maintenance(
        session_factory=ingestion_target.session_factory,
    )

    assert len(maintenance.recovered) == 1
    assert maintenance.recovered[0].status == "failed"
    assert len(maintenance.aggregations) == 1
    assert maintenance.aggregations[0].job_id == job_id
    assert maintenance.aggregations[0].status == "failed"


def assert_session_has_no_archive_state(target: IngestionTarget) -> None:
    with Session(target.engine) as database:
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == target.session_id
            )
        ) == 0
        assert database.get(SessionIngestion, target.session_id) is None
