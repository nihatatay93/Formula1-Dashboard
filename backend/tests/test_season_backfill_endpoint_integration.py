import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import (
    get_database_session_factory,
    get_fastf1_schedule_loader,
)
from app.db.engine import sqlalchemy_database_url
from app.db.models import BackfillJob, BackfillJobSession
from app.ingestion.backfill_orchestration import (
    claim_next_archive_job_session,
)
from app.ingestion.fastf1_schedule import (
    NormalizedScheduledEvent,
    NormalizedScheduledSession,
    NormalizedSeasonSchedule,
)
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@dataclass(frozen=True, slots=True)
class BackfillEndpointTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int


class ConcurrentScheduleLoader:
    def __init__(self, schedule: NormalizedSeasonSchedule) -> None:
        self._schedule = schedule
        self._lock = Lock()
        self.calls: list[int] = []

    def load(self, season_year: int) -> NormalizedSeasonSchedule:
        with self._lock:
            self.calls.append(season_year)
        return self._schedule


@pytest.fixture
def backfill_endpoint_target() -> Iterator[BackfillEndpointTarget]:
    if TEST_DATABASE_URL is None:
        pytest.skip(
            "TEST_DATABASE_URL is required for backfill endpoint integration"
        )

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(2018, :current_year) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM seasons WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            ),
            {"current_year": datetime.now(UTC).year},
        )
    assert season_year is not None

    target = BackfillEndpointTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
    )
    try:
        yield target
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM backfill_jobs WHERE season_year = :season_year"
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_ingestions
                    WHERE session_id IN (
                        SELECT sessions.id
                        FROM sessions
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM sessions
                    WHERE event_id IN (
                        SELECT id FROM events WHERE season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text("DELETE FROM events WHERE season_year = :season_year"),
                {"season_year": season_year},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :season_year"),
                {"season_year": season_year},
            )
        engine.dispose()


def _schedule(season_year: int) -> NormalizedSeasonSchedule:
    base = datetime(2020, 3, 1, 10, tzinfo=UTC)
    sessions = tuple(
        NormalizedScheduledSession(
            session_key=session_name.casefold().replace(" ", "_"),
            session_name=session_name,
            scheduled_start_at=base + timedelta(hours=index * 3),
            scheduled_end_at=base + timedelta(hours=index * 3 + 1),
        )
        for index, session_name in enumerate(
            ("Practice 1", "Qualifying", "Race")
        )
    )
    return NormalizedSeasonSchedule(
        season_year=season_year,
        events=(
            NormalizedScheduledEvent(
                round_number=1,
                official_name="FORMULA 1 API HANDOFF GRAND PRIX",
                event_name="API Handoff Grand Prix",
                country="Test Country",
                location="Test Circuit",
                event_format="conventional",
                starts_at=sessions[0].scheduled_start_at,
                ends_at=sessions[-1].scheduled_end_at,
                sessions=sessions,
            ),
        ),
    )


def test_concurrent_posts_reuse_one_worker_claimable_job(
    backfill_endpoint_target: BackfillEndpointTarget,
) -> None:
    loader = ConcurrentScheduleLoader(
        _schedule(backfill_endpoint_target.season_year)
    )
    app.dependency_overrides[get_database_session_factory] = (
        lambda: backfill_endpoint_target.session_factory
    )
    app.dependency_overrides[get_fastf1_schedule_loader] = lambda: loader
    path = (
        f"/api/v1/seasons/{backfill_endpoint_target.season_year}/backfill"
    )

    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(lambda _index: client.post(path), range(2))
            )

        assert tuple(response.status_code for response in responses) == (
            202,
            202,
        )
        payloads = tuple(response.json() for response in responses)
        job_ids = {payload["job"]["id"] for payload in payloads}
        assert len(job_ids) == 1
        assert {payload["action"] for payload in payloads} == {
            "job_created",
            "job_reused",
        }
        job_id = job_ids.pop()
        job_uuid = uuid.UUID(job_id)
        assert all(
            response.headers["location"]
            == f"/api/v1/backfill-jobs/{job_id}"
            for response in responses
        )

        with backfill_endpoint_target.session_factory() as database:
            job_count = database.scalar(
                select(func.count())
                .select_from(BackfillJob)
                .where(
                    BackfillJob.season_year
                    == backfill_endpoint_target.season_year
                )
            )
            child_count = database.scalar(
                select(func.count())
                .select_from(BackfillJobSession)
                .where(BackfillJobSession.job_id == job_uuid)
            )

        with backfill_endpoint_target.session_factory() as database:
            claim = claim_next_archive_job_session(database)

        assert job_count == 1
        assert child_count == 3
        assert claim is not None
        assert claim.job_id == job_uuid

        progress_response = client.get(f"/api/v1/backfill-jobs/{job_id}")

    assert progress_response.status_code == 200
    progress = progress_response.json()["progress"]
    assert progress == {
        "total": 3,
        "pending": 2,
        "running": 1,
        "completed": 0,
        "failed": 0,
        "terminal": 0,
    }
