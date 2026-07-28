import os
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.engine import sqlalchemy_database_url
from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    Driver,
    Lap,
    SessionEntry,
    SessionIngestion,
    SessionResult,
)
from app.ingestion.archive_persistence import (
    ArchivePersistenceOwnershipError,
    ArchivePersistenceTransactionError,
    ArchiveSourceConflictError,
    replace_archive_session,
)
from app.ingestion.backfill_orchestration import (
    ClaimedArchiveJobSession,
    claim_next_archive_job_session,
)
from app.ingestion.fastf1_normalization import (
    NormalizedDriver,
    NormalizedLap,
    NormalizedSession,
    NormalizedSessionEntry,
    NormalizedSessionResult,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for archive persistence tests",
)


@dataclass(frozen=True)
class PersistenceTarget:
    engine: Engine
    session_id: int
    event_id: int
    season_year: int
    driver_prefix: str


@pytest.fixture
def persistence_target() -> Any:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    token = uuid.uuid4().hex
    driver_prefix = f"persistence_{token}_"

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(30000, 31999) AS candidate
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
                    1,
                    :event_name,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "event_name": f"Persistence Test {token}",
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

    target = PersistenceTarget(
        engine=engine,
        session_id=session_id,
        event_id=event_id,
        season_year=season_year,
        driver_prefix=driver_prefix,
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
                    """
                    DELETE FROM backfill_job_sessions
                    WHERE session_id = :session_id
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
                    WHERE left(jolpica_driver_id, :prefix_length) = :prefix
                      AND NOT EXISTS (
                          SELECT 1
                          FROM session_entries
                          WHERE session_entries.driver_id = drivers.id
                      )
                    """
                ),
                {
                    "prefix": driver_prefix,
                    "prefix_length": len(driver_prefix),
                },
            )
        engine.dispose()


def make_snapshot(target: PersistenceTarget) -> NormalizedSession:
    first_driver_id = f"{target.driver_prefix}first"
    second_driver_id = f"{target.driver_prefix}second"
    first_key = f"driver:jolpica:{first_driver_id}"
    second_key = f"driver:jolpica:{second_driver_id}"

    drivers = (
        make_driver(first_driver_id, "First Driver"),
        make_driver(second_driver_id, "Second Driver"),
    )
    entries = (
        make_entry(first_key, first_driver_id, "11", "FIR", "First Driver"),
        make_entry(second_key, second_driver_id, "22", "SEC", "Second Driver"),
    )
    results = (
        make_result(first_key, position=1, points=Decimal("25")),
        make_result(second_key, position=2, points=Decimal("18")),
    )
    laps = (
        make_lap(first_key, lap_number=1, lap_time_us=90_000_000),
        make_lap(first_key, lap_number=2, lap_time_us=89_000_000),
        make_lap(second_key, lap_number=1, lap_time_us=91_000_000),
    )
    return NormalizedSession(
        drivers=drivers,
        entries=entries,
        results=results,
        laps=laps,
    )


def make_driver(driver_id: str, full_name: str) -> NormalizedDriver:
    given_name, family_name = full_name.split(" ", maxsplit=1)
    return NormalizedDriver(
        jolpica_driver_id=driver_id,
        given_name=given_name,
        family_name=family_name,
        full_name=full_name,
        country_code="GBR",
    )


def make_entry(
    entry_key: str,
    driver_id: str | None,
    racing_number: str,
    abbreviation: str,
    display_name: str,
) -> NormalizedSessionEntry:
    return NormalizedSessionEntry(
        entry_key=entry_key,
        jolpica_driver_id=driver_id,
        racing_number=racing_number,
        abbreviation=abbreviation,
        broadcast_name=display_name.upper(),
        display_name=display_name,
        team_jolpica_id="persistence_team",
        team_name="Persistence Team",
        team_color="112233",
    )


def make_result(
    entry_key: str,
    *,
    position: int,
    points: Decimal,
) -> NormalizedSessionResult:
    return NormalizedSessionResult(
        entry_key=entry_key,
        position=position,
        classified_position=str(position),
        grid_position=position,
        points=points,
        status="Finished",
        laps_completed=2,
        q1_time_us=None,
        q2_time_us=None,
        q3_time_us=None,
        elapsed_time_us=180_000_000 if position == 1 else None,
        gap_to_leader_us=0 if position == 1 else 2_000_000,
        gap_to_leader_laps=0,
    )


def make_lap(
    entry_key: str,
    *,
    lap_number: int,
    lap_time_us: int,
) -> NormalizedLap:
    return NormalizedLap(
        entry_key=entry_key,
        lap_number=lap_number,
        stint_number=1,
        session_time_us=lap_number * 100_000_000,
        lap_time_us=lap_time_us,
        lap_start_time_us=(lap_number - 1) * 100_000_000,
        pit_out_time_us=None,
        pit_in_time_us=None,
        sector_1_time_us=30_000_000,
        sector_2_time_us=30_000_000,
        sector_3_time_us=lap_time_us - 60_000_000,
        sector_1_session_time_us=None,
        sector_2_session_time_us=None,
        sector_3_session_time_us=None,
        speed_i1_kph=280.0,
        speed_i2_kph=290.0,
        speed_fl_kph=300.0,
        speed_st_kph=310.0,
        is_personal_best=lap_number == 2,
        compound="MEDIUM",
        tyre_life_laps=lap_number,
        fresh_tyre=lap_number == 1,
        track_status="1",
        position=1,
        deleted=None,
        deleted_reason=None,
        fastf1_generated=False,
        is_accurate=True,
    )


def claim_persistence_target(
    target: PersistenceTarget,
) -> ClaimedArchiveJobSession:
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
                "session_id": target.session_id,
            },
        )

    with Session(target.engine) as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None
    assert claim.job_id == job_id
    assert claim.session_id == target.session_id
    return claim


def test_claimed_completion_is_atomic_with_archive_replacement(
    persistence_target: PersistenceTarget,
) -> None:
    claim = claim_persistence_target(persistence_target)
    completed_at = datetime(2026, 7, 28, 21, 0, tzinfo=UTC)
    source_updated_at = datetime(2026, 7, 28, 20, 30, tzinfo=UTC)

    with persistence_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE backfill_job_sessions
                SET last_error_code = 'previous_failure',
                    last_error_message = 'Previous fixed failure.'
                WHERE job_id = :job_id
                  AND session_id = :session_id
                """
            ),
            {
                "job_id": claim.job_id,
                "session_id": claim.session_id,
            },
        )
        connection.execute(
            text(
                """
                UPDATE session_ingestions
                SET last_error_code = 'previous_failure',
                    last_error_message = 'Previous fixed failure.'
                WHERE session_id = :session_id
                """
            ),
            {"session_id": claim.session_id},
        )

    with Session(persistence_target.engine) as database:
        summary = replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=make_snapshot(persistence_target),
            completed_at=completed_at,
            source_updated_at=source_updated_at,
            claim=claim,
        )

    assert summary.session_id == persistence_target.session_id
    with Session(persistence_target.engine) as database:
        job = database.get(BackfillJob, claim.job_id)
        job_session = database.get(
            BackfillJobSession,
            (claim.job_id, claim.session_id),
        )
        ingestion = database.get(SessionIngestion, claim.session_id)

        assert job is not None
        assert job.status == "running"
        assert job.heartbeat_at is not None
        assert job.heartbeat_at >= claim.claimed_at

        assert job_session is not None
        assert job_session.status == "completed"
        assert job_session.attempt_count == claim.job_attempt_count
        assert job_session.completed_at == completed_at
        assert job_session.heartbeat_at is None
        assert job_session.next_retry_at is None
        assert job_session.last_error_code is None
        assert job_session.last_error_message is None

        assert ingestion is not None
        assert ingestion.status == "completed"
        assert ingestion.attempt_count == claim.session_attempt_token
        assert ingestion.completed_at == completed_at
        assert ingestion.source_updated_at == source_updated_at
        assert ingestion.heartbeat_at is None
        assert ingestion.next_retry_at is None
        assert ingestion.last_error_code is None
        assert ingestion.last_error_message is None
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == claim.session_id
            )
        ) == 2


@pytest.mark.parametrize(
    "ownership_field",
    ["job_attempt_count", "session_attempt_token"],
)
def test_claimed_completion_rejects_stale_ownership_before_writes(
    persistence_target: PersistenceTarget,
    ownership_field: str,
) -> None:
    claim = claim_persistence_target(persistence_target)
    stale_claim = replace(
        claim,
        **{
            ownership_field: getattr(claim, ownership_field) + 1,
        },
    )

    with Session(persistence_target.engine) as database:
        with pytest.raises(
            ArchivePersistenceOwnershipError,
            match="no longer owns",
        ):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=make_snapshot(persistence_target),
                claim=stale_claim,
            )

    with Session(persistence_target.engine) as database:
        job_session = database.get(
            BackfillJobSession,
            (claim.job_id, claim.session_id),
        )
        ingestion = database.get(SessionIngestion, claim.session_id)

        assert job_session is not None
        assert job_session.status == "running"
        assert job_session.completed_at is None
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.completed_at is None
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == claim.session_id
            )
        ) == 0
        assert database.scalar(
            select(func.count(Driver.id)).where(
                Driver.jolpica_driver_id.in_(
                    [
                        driver.jolpica_driver_id
                        for driver in make_snapshot(persistence_target).drivers
                    ]
                )
            )
        ) == 0


def test_claimed_database_failure_rolls_back_snapshot_and_completion(
    persistence_target: PersistenceTarget,
) -> None:
    claim = claim_persistence_target(persistence_target)
    snapshot = make_snapshot(persistence_target)
    invalid_snapshot = replace(
        snapshot,
        results=(
            replace(snapshot.results[0], points=Decimal("-1")),
            snapshot.results[1],
        ),
    )

    with Session(persistence_target.engine) as database:
        with pytest.raises(IntegrityError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=invalid_snapshot,
                claim=claim,
            )

    with Session(persistence_target.engine) as database:
        job_session = database.get(
            BackfillJobSession,
            (claim.job_id, claim.session_id),
        )
        ingestion = database.get(SessionIngestion, claim.session_id)

        assert job_session is not None
        assert job_session.status == "running"
        assert job_session.completed_at is None
        assert job_session.heartbeat_at == claim.claimed_at
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.completed_at is None
        assert ingestion.heartbeat_at == claim.claimed_at
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == claim.session_id
            )
        ) == 0


def test_persists_and_idempotently_updates_one_snapshot(
    persistence_target: PersistenceTarget,
) -> None:
    snapshot = make_snapshot(persistence_target)
    first_completed_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    second_completed_at = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    source_updated_at = datetime(2026, 7, 28, 11, 30, tzinfo=UTC)

    with Session(persistence_target.engine) as database:
        first_summary = replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=snapshot,
            completed_at=first_completed_at,
            source_updated_at=source_updated_at,
        )

    assert first_summary.drivers_upserted == 2
    assert first_summary.entries_upserted == 2
    assert first_summary.results_upserted == 2
    assert first_summary.laps_upserted == 3
    assert first_summary.stale_entries_deleted == 0
    assert first_summary.stale_results_deleted == 0
    assert first_summary.stale_laps_deleted == 0

    first_entry_ids, first_lap_ids = stored_natural_key_ids(persistence_target)

    with Session(persistence_target.engine) as database:
        second_summary = replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=snapshot,
            completed_at=second_completed_at,
        )

    second_entry_ids, second_lap_ids = stored_natural_key_ids(persistence_target)
    assert second_entry_ids == first_entry_ids
    assert second_lap_ids == first_lap_ids
    assert second_summary.stale_entries_deleted == 0
    assert second_summary.stale_results_deleted == 0
    assert second_summary.stale_laps_deleted == 0

    with Session(persistence_target.engine) as database:
        ingestion = database.get(SessionIngestion, persistence_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "completed"
        assert ingestion.source == "fastf1_archive"
        assert ingestion.record_state == "finalized"
        assert ingestion.completed_at == second_completed_at
        assert ingestion.source_updated_at == source_updated_at
        assert ingestion.last_error_code is None
        assert database.scalar(
            select(func.count(SessionResult.session_entry_id)).join(
                SessionEntry,
                SessionEntry.id == SessionResult.session_entry_id,
            ).where(SessionEntry.session_id == persistence_target.session_id)
        ) == 2


def test_replacement_updates_retained_rows_and_removes_stale_rows(
    persistence_target: PersistenceTarget,
) -> None:
    initial = make_snapshot(persistence_target)
    with Session(persistence_target.engine) as database:
        replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=initial,
        )

    initial_entry_ids, initial_lap_ids = stored_natural_key_ids(persistence_target)
    retained_entry = replace(
        initial.entries[0],
        racing_number="33",
        display_name="Updated Driver",
        team_name="Updated Team",
    )
    retained_driver = replace(initial.drivers[0], full_name="Updated Driver")
    retained_result = replace(initial.results[0], points=Decimal("26"))
    retained_lap = replace(initial.laps[0], lap_time_us=88_000_000)
    replacement_snapshot = NormalizedSession(
        drivers=(retained_driver,),
        entries=(retained_entry,),
        results=(retained_result,),
        laps=(retained_lap,),
    )

    with Session(persistence_target.engine) as database:
        summary = replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=replacement_snapshot,
        )

    assert summary.stale_entries_deleted == 1
    assert summary.stale_results_deleted == 1
    assert summary.stale_laps_deleted == 2

    entry_ids, lap_ids = stored_natural_key_ids(persistence_target)
    retained_key = retained_entry.entry_key
    assert entry_ids == {retained_key: initial_entry_ids[retained_key]}
    assert lap_ids == {
        (retained_key, 1): initial_lap_ids[(retained_key, 1)]
    }

    with Session(persistence_target.engine) as database:
        entry = database.scalar(
            select(SessionEntry).where(
                SessionEntry.session_id == persistence_target.session_id
            )
        )
        assert entry is not None
        assert entry.racing_number == "33"
        assert entry.display_name == "Updated Driver"
        assert entry.team_name == "Updated Team"

        result = database.get(SessionResult, entry.id)
        assert result is not None
        assert result.points == Decimal("26.000")

        lap = database.scalar(
            select(Lap).where(Lap.session_entry_id == entry.id)
        )
        assert lap is not None
        assert lap.lap_time_us == 88_000_000

        assert database.scalar(
            select(func.count(Driver.id)).where(
                Driver.jolpica_driver_id
                == initial.drivers[1].jolpica_driver_id
            )
        ) == 1


def test_replacement_supports_fallback_to_verified_driver_key_transition(
    persistence_target: PersistenceTarget,
) -> None:
    unresolved_key = "car-number:7"
    unresolved_snapshot = NormalizedSession(
        drivers=(),
        entries=(
            make_entry(
                unresolved_key,
                None,
                "7",
                "UNK",
                "Unresolved Driver",
            ),
        ),
        results=(
            make_result(
                unresolved_key,
                position=1,
                points=Decimal("25"),
            ),
        ),
        laps=(make_lap(unresolved_key, lap_number=1, lap_time_us=90_000_000),),
    )
    with Session(persistence_target.engine) as database:
        replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=unresolved_snapshot,
        )

    resolved_driver_id = f"{persistence_target.driver_prefix}resolved"
    resolved_key = f"driver:jolpica:{resolved_driver_id}"
    resolved_snapshot = NormalizedSession(
        drivers=(make_driver(resolved_driver_id, "Resolved Driver"),),
        entries=(
            make_entry(
                resolved_key,
                resolved_driver_id,
                "7",
                "RES",
                "Resolved Driver",
            ),
        ),
        results=(
            make_result(
                resolved_key,
                position=1,
                points=Decimal("25"),
            ),
        ),
        laps=(make_lap(resolved_key, lap_number=1, lap_time_us=89_000_000),),
    )

    with Session(persistence_target.engine) as database:
        summary = replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=resolved_snapshot,
        )

    assert summary.stale_entries_deleted == 1
    assert summary.stale_results_deleted == 1
    assert summary.stale_laps_deleted == 1

    with Session(persistence_target.engine) as database:
        entry = database.scalar(
            select(SessionEntry).where(
                SessionEntry.session_id == persistence_target.session_id
            )
        )
        assert entry is not None
        assert entry.entry_key == resolved_key
        assert entry.racing_number == "7"
        assert entry.driver_id is not None
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == persistence_target.session_id
            )
        ) == 1


def test_replacement_refuses_non_archive_session_data(
    persistence_target: PersistenceTarget,
) -> None:
    with persistence_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_entries (
                    session_id,
                    entry_key,
                    racing_number,
                    display_name,
                    source,
                    record_state
                )
                VALUES (
                    :session_id,
                    'live:44',
                    '44',
                    'Live Driver',
                    'live_signalr',
                    'provisional'
                )
                """
            ),
            {"session_id": persistence_target.session_id},
        )

    with Session(persistence_target.engine) as database:
        with pytest.raises(ArchiveSourceConflictError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=make_snapshot(persistence_target),
            )

    with Session(persistence_target.engine) as database:
        assert database.scalar(
            select(func.count(SessionEntry.id)).where(
                SessionEntry.session_id == persistence_target.session_id
            )
        ) == 1
        assert database.scalar(
            select(func.count(Driver.id)).where(
                Driver.jolpica_driver_id.in_(
                    [
                        driver.jolpica_driver_id
                        for driver in make_snapshot(persistence_target).drivers
                    ]
                )
            )
        ) == 0


def test_replacement_refuses_non_archive_child_data(
    persistence_target: PersistenceTarget,
) -> None:
    with persistence_target.engine.begin() as connection:
        entry_id = connection.scalar(
            text(
                """
                INSERT INTO session_entries (
                    session_id,
                    entry_key,
                    racing_number,
                    display_name,
                    source,
                    record_state
                )
                VALUES (
                    :session_id,
                    'archive:44',
                    '44',
                    'Archive Entry',
                    'fastf1_archive',
                    'finalized'
                )
                RETURNING id
                """
            ),
            {"session_id": persistence_target.session_id},
        )
        assert entry_id is not None
        connection.execute(
            text(
                """
                INSERT INTO session_results (
                    session_entry_id,
                    source,
                    record_state
                )
                VALUES (
                    :entry_id,
                    'live_signalr',
                    'provisional'
                )
                """
            ),
            {"entry_id": entry_id},
        )

    with Session(persistence_target.engine) as database:
        with pytest.raises(ArchiveSourceConflictError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=make_snapshot(persistence_target),
            )

    with Session(persistence_target.engine) as database:
        stored_result = database.get(SessionResult, entry_id)
        assert stored_result is not None
        assert stored_result.source == "live_signalr"
        assert stored_result.record_state == "provisional"


def test_replacement_refuses_non_archive_ingestion_state(
    persistence_target: PersistenceTarget,
) -> None:
    with persistence_target.engine.begin() as connection:
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
            {"session_id": persistence_target.session_id},
        )

    with Session(persistence_target.engine) as database:
        with pytest.raises(ArchiveSourceConflictError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=make_snapshot(persistence_target),
            )

    with Session(persistence_target.engine) as database:
        ingestion = database.get(SessionIngestion, persistence_target.session_id)
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.source == "live_signalr"
        assert ingestion.record_state == "provisional"


def test_database_constraint_failure_rolls_back_complete_replacement(
    persistence_target: PersistenceTarget,
) -> None:
    initial = make_snapshot(persistence_target)
    initial_completed_at = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    with Session(persistence_target.engine) as database:
        replace_archive_session(
            database,
            session_id=persistence_target.session_id,
            snapshot=initial,
            completed_at=initial_completed_at,
        )

    invalid_snapshot = replace(
        initial,
        drivers=(
            replace(initial.drivers[0], full_name="Rolled Back Driver"),
            initial.drivers[1],
        ),
        entries=(
            replace(initial.entries[0], display_name="Rolled Back Driver"),
            initial.entries[1],
        ),
        results=(
            replace(initial.results[0], points=Decimal("-1")),
            initial.results[1],
        ),
    )

    with Session(persistence_target.engine) as database:
        with pytest.raises(IntegrityError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=invalid_snapshot,
                completed_at=datetime(2026, 7, 28, 14, 0, tzinfo=UTC),
            )

    with Session(persistence_target.engine) as database:
        entry = database.scalar(
            select(SessionEntry).where(
                SessionEntry.session_id == persistence_target.session_id,
                SessionEntry.entry_key == initial.entries[0].entry_key,
            )
        )
        assert entry is not None
        assert entry.display_name == "First Driver"

        driver = database.scalar(
            select(Driver).where(
                Driver.jolpica_driver_id
                == initial.drivers[0].jolpica_driver_id
            )
        )
        assert driver is not None
        assert driver.full_name == "First Driver"

        result = database.get(SessionResult, entry.id)
        assert result is not None
        assert result.points == Decimal("25.000")

        ingestion = database.get(SessionIngestion, persistence_target.session_id)
        assert ingestion is not None
        assert ingestion.completed_at == initial_completed_at
        assert database.scalar(
            select(func.count(Lap.id))
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .where(SessionEntry.session_id == persistence_target.session_id)
        ) == 3


def test_replacement_requires_transaction_ownership(
    persistence_target: PersistenceTarget,
) -> None:
    with Session(persistence_target.engine) as database:
        database.execute(select(1))
        with pytest.raises(ArchivePersistenceTransactionError):
            replace_archive_session(
                database,
                session_id=persistence_target.session_id,
                snapshot=make_snapshot(persistence_target),
            )


def stored_natural_key_ids(
    target: PersistenceTarget,
) -> tuple[dict[str, int], dict[tuple[str, int], int]]:
    with Session(target.engine) as database:
        entry_rows = database.execute(
            select(SessionEntry.entry_key, SessionEntry.id).where(
                SessionEntry.session_id == target.session_id
            )
        ).all()
        entry_ids = {
            entry_key: entry_id
            for entry_key, entry_id in entry_rows
        }
        lap_rows = database.execute(
            select(SessionEntry.entry_key, Lap.lap_number, Lap.id)
            .join(Lap, Lap.session_entry_id == SessionEntry.id)
            .where(SessionEntry.session_id == target.session_id)
        ).all()
        lap_ids = {
            (entry_key, lap_number): lap_id
            for entry_key, lap_number, lap_id in lap_rows
        }
    return entry_ids, lap_ids
