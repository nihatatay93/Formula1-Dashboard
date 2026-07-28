import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    Event,
    RaceSession,
    Season,
)
from app.ingestion.backfill_orchestration import (
    claim_next_archive_job_session,
)
from app.ingestion.fastf1_schedule import (
    DeferredFutureEvent,
    NormalizedScheduledEvent,
    NormalizedScheduledSession,
    NormalizedSeasonSchedule,
)
from app.ingestion.freshness_policy import CoverageRefreshReason
from app.ingestion.season_backfill import (
    SeasonBackfillSnapshotError,
    SeasonBackfillSourceConflictError,
    ensure_season_backfill,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for season backfill tests",
)


@dataclass(frozen=True, slots=True)
class SeasonTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int


class StubScheduleLoader:
    def __init__(
        self,
        schedules: list[NormalizedSeasonSchedule],
    ) -> None:
        self._schedules = schedules
        self._lock = Lock()
        self.calls: list[int] = []

    def load(self, season_year: int) -> NormalizedSeasonSchedule:
        with self._lock:
            self.calls.append(season_year)
            if len(self._schedules) == 1:
                return self._schedules[0]
            return self._schedules.pop(0)


@pytest.fixture
def season_target() -> Iterator[SeasonTarget]:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(28000, 29999) AS candidate
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
    target = SeasonTarget(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            expire_on_commit=False,
        ),
        season_year=season_year,
    )
    try:
        yield target
    finally:
        with engine.begin() as connection:
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
                        SELECT id
                        FROM events
                        WHERE season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    "DELETE FROM events WHERE season_year = :season_year"
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :season_year"),
                {"season_year": season_year},
            )
        engine.dispose()


def schedule(
    season_year: int,
    *,
    session_names: tuple[str, ...] = ("Practice 1", "Qualifying", "Race"),
    ended: bool = True,
) -> NormalizedSeasonSchedule:
    base = (
        datetime(2020, 3, 1, 10, tzinfo=UTC)
        if ended
        else datetime(2099, 3, 1, 10, tzinfo=UTC)
    )
    sessions = tuple(
        NormalizedScheduledSession(
            session_key=name.casefold().replace(" ", "_"),
            session_name=name,
            scheduled_start_at=base + timedelta(hours=index * 3),
            scheduled_end_at=base + timedelta(hours=index * 3 + 1),
        )
        for index, name in enumerate(session_names)
    )
    event = NormalizedScheduledEvent(
        round_number=1,
        official_name="FORMULA 1 TEST GRAND PRIX",
        event_name="Test Grand Prix",
        country="Test Country",
        location="Test Circuit",
        event_format="conventional",
        starts_at=sessions[0].scheduled_start_at,
        ends_at=sessions[-1].scheduled_end_at,
        sessions=sessions,
    )
    return NormalizedSeasonSchedule(
        season_year=season_year,
        events=(event,),
    )


def test_missing_season_persists_schedule_and_creates_one_job(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader([schedule(season_target.season_year)])

    plan = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert loader.calls == [season_target.season_year]
    assert plan.coverage_reason is CoverageRefreshReason.MISSING
    assert plan.coverage_refreshed is True
    assert plan.coverage_checked_at is not None
    assert plan.coverage_valid_until is not None
    assert plan.job_id is not None
    assert plan.job_status == "pending"
    assert plan.job_created is True
    assert plan.eligible_session_ids == plan.newly_queued_session_ids
    assert len(plan.eligible_session_ids) == 3

    with season_target.session_factory() as database:
        season = database.get(Season, season_target.season_year)
        events = database.scalars(
            select(Event).where(
                Event.season_year == season_target.season_year
            )
        ).all()
        sessions = database.scalars(
            select(RaceSession)
            .join(Event, Event.id == RaceSession.event_id)
            .where(Event.season_year == season_target.season_year)
        ).all()
        job = database.get(BackfillJob, plan.job_id)
        job_sessions = database.scalars(
            select(BackfillJobSession).where(
                BackfillJobSession.job_id == plan.job_id
            )
        ).all()

    assert season is not None
    assert season.coverage_checked_at == plan.coverage_checked_at
    assert all(
        event.last_discovered_at == season.coverage_checked_at
        for event in events
    )
    assert all(
        race_session.last_discovered_at == season.coverage_checked_at
        for race_session in sessions
    )
    assert job is not None
    assert job.request_reason == "missing"
    assert len(job_sessions) == 3


def test_fresh_repeat_reuses_active_job_without_loading_or_duplicates(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader([schedule(season_target.season_year)])
    first = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )
    second = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert loader.calls == [season_target.season_year]
    assert second.coverage_reason is CoverageRefreshReason.FRESH
    assert second.coverage_refreshed is False
    assert second.job_id == first.job_id
    assert second.job_created is False
    assert second.newly_queued_session_ids == ()

    with season_target.session_factory() as database:
        job_count = database.scalar(
            select(func.count())
            .select_from(BackfillJob)
            .where(
                BackfillJob.season_year == season_target.season_year
            )
        )
        child_count = database.scalar(
            select(func.count())
            .select_from(BackfillJobSession)
            .where(BackfillJobSession.job_id == first.job_id)
        )

    assert job_count == 1
    assert child_count == 3


def test_planned_session_is_claimable_by_worker_orchestration(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader(
        [
            schedule(
                season_target.season_year,
                session_names=("Race",),
            )
        ]
    )
    plan = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    with season_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)

    assert claim is not None
    assert claim.job_id == plan.job_id
    assert claim.session_id == plan.newly_queued_session_ids[0]
    assert claim.job_attempt_count == 1
    assert claim.session_attempt_token == 1


def test_stale_refresh_appends_new_session_to_running_job(
    season_target: SeasonTarget,
) -> None:
    first_schedule = schedule(
        season_target.season_year,
        session_names=("Race",),
    )
    second_schedule = schedule(
        season_target.season_year,
        session_names=("Qualifying", "Race"),
    )
    loader = StubScheduleLoader([first_schedule, second_schedule])
    first = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )
    assert first.job_id is not None

    with season_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE seasons
                SET coverage_valid_until = clock_timestamp() - interval '1 second'
                WHERE year = :season_year
                """
            ),
            {"season_year": season_target.season_year},
        )
        connection.execute(
            text(
                """
                UPDATE backfill_jobs
                SET status = 'running',
                    started_at = clock_timestamp()
                WHERE id = :job_id
                """
            ),
            {"job_id": first.job_id},
        )

    refreshed = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert refreshed.coverage_reason is CoverageRefreshReason.STALE
    assert refreshed.coverage_refreshed is True
    assert refreshed.job_id == first.job_id
    assert refreshed.job_created is False
    assert len(refreshed.newly_queued_session_ids) == 1


def test_future_sessions_refresh_coverage_without_creating_job(
    season_target: SeasonTarget,
) -> None:
    available_schedule = schedule(
        season_target.season_year,
        ended=False,
    )
    deferred_event = DeferredFutureEvent(
        round_number=2,
        event_name="Future Grand Prix",
        scheduled_start_at=datetime(2099, 4, 1, 10, tzinfo=UTC),
    )
    loader = StubScheduleLoader(
        [
            NormalizedSeasonSchedule(
                season_year=available_schedule.season_year,
                events=available_schedule.events,
                deferred_future_events=(deferred_event,),
            )
        ]
    )

    plan = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert plan.coverage_refreshed is True
    assert plan.eligible_session_ids == ()
    assert plan.job_id is None
    assert plan.job_created is False
    assert plan.deferred_future_events == (deferred_event,)


def test_unowned_pending_ingestion_does_not_create_empty_job(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader([schedule(season_target.season_year)])
    initial = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )
    assert initial.job_id is not None

    with season_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE backfill_job_sessions
                SET status = 'failed'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": initial.job_id},
        )
        connection.execute(
            text(
                """
                UPDATE backfill_jobs
                SET status = 'failed',
                    completed_at = clock_timestamp()
                WHERE id = :job_id
                """
            ),
            {"job_id": initial.job_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state
                )
                SELECT
                    sessions.id,
                    'pending',
                    'fastf1_archive',
                    'finalized'
                FROM sessions
                JOIN events ON events.id = sessions.event_id
                WHERE events.season_year = :season_year
                """
            ),
            {"season_year": season_target.season_year},
        )

    plan = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert plan.eligible_session_ids == ()
    assert plan.job_id is None
    with season_target.session_factory() as database:
        active_jobs = database.scalars(
            select(BackfillJob).where(
                BackfillJob.season_year == season_target.season_year,
                BackfillJob.status.in_(("pending", "running")),
            )
        ).all()
    assert active_jobs == []


def test_due_correction_on_fresh_coverage_creates_stale_job(
    season_target: SeasonTarget,
) -> None:
    initial_loader = StubScheduleLoader(
        [schedule(season_target.season_year)]
    )
    initial = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=initial_loader,
    )
    assert initial.job_id is not None

    with season_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE backfill_jobs
                SET status = 'completed',
                    completed_at = clock_timestamp()
                WHERE id = :job_id
                """
            ),
            {"job_id": initial.job_id},
        )
        connection.execute(
            text(
                """
                UPDATE backfill_job_sessions
                SET status = 'completed',
                    completed_at = clock_timestamp()
                WHERE job_id = :job_id
                """
            ),
            {"job_id": initial.job_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count,
                    completed_at
                )
                SELECT
                    sessions.id,
                    'completed',
                    'fastf1_archive',
                    'finalized',
                    1,
                    sessions.scheduled_end_at + interval '3 hours'
                FROM sessions
                JOIN events ON events.id = sessions.event_id
                WHERE events.season_year = :season_year
                """
            ),
            {"season_year": season_target.season_year},
        )

    fresh_loader = StubScheduleLoader(
        [schedule(season_target.season_year)]
    )
    correction = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=fresh_loader,
    )

    assert fresh_loader.calls == []
    assert correction.coverage_reason is CoverageRefreshReason.FRESH
    assert correction.job_created is True
    assert correction.job_id != initial.job_id
    assert len(correction.newly_queued_session_ids) == 3

    with season_target.session_factory() as database:
        job = database.get(BackfillJob, correction.job_id)
    assert job is not None
    assert job.request_reason == "stale"


def test_removed_session_is_preserved_but_not_queued_again(
    season_target: SeasonTarget,
) -> None:
    first_schedule = schedule(
        season_target.season_year,
        session_names=("Practice 1", "Race"),
    )
    second_schedule = schedule(
        season_target.season_year,
        session_names=("Race",),
    )
    loader = StubScheduleLoader([first_schedule, second_schedule])
    first = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )
    assert first.job_id is not None

    with season_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE backfill_job_sessions
                SET status = 'failed'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": first.job_id},
        )
        connection.execute(
            text(
                """
                UPDATE backfill_jobs
                SET status = 'failed',
                    completed_at = clock_timestamp()
                WHERE id = :job_id
                """
            ),
            {"job_id": first.job_id},
        )
        connection.execute(
            text(
                """
                UPDATE seasons
                SET coverage_valid_until = clock_timestamp() - interval '1 second'
                WHERE year = :season_year
                """
            ),
            {"season_year": season_target.season_year},
        )

    refreshed = ensure_season_backfill(
        season_year=season_target.season_year,
        session_factory=season_target.session_factory,
        schedule_loader=loader,
    )

    assert len(refreshed.eligible_session_ids) == 1
    assert len(refreshed.newly_queued_session_ids) == 1
    with season_target.session_factory() as database:
        stored_sessions = database.scalars(
            select(RaceSession)
            .join(Event, Event.id == RaceSession.event_id)
            .where(Event.season_year == season_target.season_year)
            .order_by(RaceSession.session_key)
        ).all()
        season = database.get(Season, season_target.season_year)

    assert len(stored_sessions) == 2
    assert season is not None
    current_keys = {
        race_session.session_key
        for race_session in stored_sessions
        if race_session.last_discovered_at == season.coverage_checked_at
    }
    assert current_keys == {"race"}


def test_source_conflict_rolls_back_snapshot_and_coverage(
    season_target: SeasonTarget,
) -> None:
    with season_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO seasons (year)
                VALUES (:season_year)
                """
            ),
            {"season_year": season_target.season_year},
        )
        connection.execute(
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
                    1,
                    'Live Event',
                    'live_signalr'
                )
                """
            ),
            {"season_year": season_target.season_year},
        )

    loader = StubScheduleLoader([schedule(season_target.season_year)])
    with pytest.raises(SeasonBackfillSourceConflictError):
        ensure_season_backfill(
            season_year=season_target.season_year,
            session_factory=season_target.session_factory,
            schedule_loader=loader,
        )

    with season_target.session_factory() as database:
        season = database.get(Season, season_target.season_year)
        jobs = database.scalars(
            select(BackfillJob).where(
                BackfillJob.season_year == season_target.season_year
            )
        ).all()

    assert season is not None
    assert season.coverage_checked_at is None
    assert season.coverage_valid_until is None
    assert jobs == []


def test_concurrent_requests_reuse_one_active_job(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader([schedule(season_target.season_year)])

    def ensure() -> object:
        return ensure_season_backfill(
            season_year=season_target.season_year,
            session_factory=season_target.session_factory,
            schedule_loader=loader,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        plans = list(executor.map(lambda _value: ensure(), range(2)))

    assert plans[0].job_id == plans[1].job_id
    assert sum(plan.job_created for plan in plans) == 1
    with season_target.session_factory() as database:
        job_count = database.scalar(
            select(func.count())
            .select_from(BackfillJob)
            .where(
                BackfillJob.season_year == season_target.season_year,
                BackfillJob.status.in_(("pending", "running")),
            )
        )
    assert job_count == 1


def test_rejects_mismatched_loaded_season_without_writes(
    season_target: SeasonTarget,
) -> None:
    loader = StubScheduleLoader(
        [schedule(season_target.season_year + 1)]
    )

    with pytest.raises(SeasonBackfillSnapshotError):
        ensure_season_backfill(
            season_year=season_target.season_year,
            session_factory=season_target.session_factory,
            schedule_loader=loader,
        )

    with season_target.session_factory() as database:
        assert database.get(Season, season_target.season_year) is None
