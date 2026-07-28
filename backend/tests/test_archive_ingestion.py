import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.db.models import Lap, SessionEntry, SessionIngestion, SessionResult
from app.ingestion.archive_ingestion import (
    ArchiveSessionIdentityError,
    ingest_fastf1_archive_session,
)
from app.ingestion.archive_persistence import (
    ArchivePersistenceTargetChangedError,
    ArchiveSessionNotFoundError,
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


def assert_session_has_no_archive_state(target: IngestionTarget) -> None:
    with Session(target.engine) as database:
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == target.session_id
            )
        ) == 0
        assert database.get(SessionIngestion, target.session_id) is None
