import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.db.models import (
    LapTelemetryIngestion,
    LapTelemetrySample,
    SessionIngestion,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings
from app.ingestion.telemetry_ingestion import (
    TelemetryClaimOwnershipError,
    claim_next_telemetry_lap,
    ensure_lap_telemetry,
    heartbeat_telemetry_lap,
    recover_stale_telemetry_leases,
    replace_lap_telemetry,
)
from app.ingestion.telemetry_normalization import NormalizedTelemetrySample

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for telemetry ingestion tests",
)


@pytest.fixture
def seeded_target():
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    factory = sessionmaker(engine, expire_on_commit=False)
    marker = datetime.now(UTC)
    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                "INSERT INTO seasons (year) VALUES (31991) "
                "ON CONFLICT (year) DO UPDATE SET year = EXCLUDED.year "
                "RETURNING year"
            )
        )
        event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year, round_number, event_name, source
                )
                VALUES (:year, 1, 'Telemetry Test GP', 'fastf1_archive')
                ON CONFLICT (season_year, round_number) DO UPDATE
                SET event_name = EXCLUDED.event_name
                RETURNING id
                """
            ),
            {"year": season_year},
        )
        session_id = connection.scalar(
            text(
                """
                INSERT INTO sessions (
                    event_id, session_key, session_name, source
                )
                VALUES (:event_id, 'race', 'Race', 'fastf1_archive')
                ON CONFLICT (event_id, session_key) DO UPDATE
                SET session_name = EXCLUDED.session_name
                RETURNING id
                """
            ),
            {"event_id": event_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id, status, source, record_state, completed_at
                )
                VALUES (
                    :session_id, 'completed', 'fastf1_archive',
                    'finalized', :completed_at
                )
                ON CONFLICT (session_id) DO UPDATE
                SET status = 'completed',
                    completed_at = EXCLUDED.completed_at
                """
            ),
            {"session_id": session_id, "completed_at": marker},
        )
        entry_id = connection.scalar(
            text(
                """
                INSERT INTO session_entries (
                    session_id, entry_key, racing_number, abbreviation,
                    display_name, source, record_state
                )
                VALUES (
                    :session_id, 'number:4', '4', 'NOR',
                    'Lando Norris', 'fastf1_archive', 'finalized'
                )
                ON CONFLICT (session_id, entry_key) DO UPDATE
                SET racing_number = EXCLUDED.racing_number
                RETURNING id
                """
            ),
            {"session_id": session_id},
        )
        lap_id = connection.scalar(
            text(
                """
                INSERT INTO laps (
                    session_entry_id, lap_number, fastf1_generated,
                    is_accurate, source, record_state
                )
                VALUES (
                    :entry_id, 7, FALSE, TRUE,
                    'fastf1_archive', 'finalized'
                )
                ON CONFLICT (session_entry_id, lap_number) DO UPDATE
                SET is_accurate = EXCLUDED.is_accurate
                RETURNING id
                """
            ),
            {"entry_id": entry_id},
        )
        connection.execute(
            text(
                "DELETE FROM lap_telemetry_ingestions WHERE lap_id = :lap_id"
            ),
            {"lap_id": lap_id},
        )
    try:
        yield factory, session_id, entry_id, lap_id, marker
    finally:
        engine.dispose()


def _sample(index: int = 0) -> NormalizedTelemetrySample:
    return NormalizedTelemetrySample(
        sample_index=index,
        lap_time_us=10_000 + index * 240_000,
        session_time_us=100_000_000 + index * 240_000,
        distance_m=1.0 + index,
        relative_distance=0.01 + index * 0.01,
        speed_kph=250.0,
        rpm=11_000,
        gear=7,
        throttle_percent=100.0,
        brake=False,
        drs=10,
        x=None,
        y=None,
        z=None,
    )


def test_duplicate_commands_converge_on_one_persistent_request(
    seeded_target,
) -> None:
    factory, session_id, entry_id, lap_id, marker = seeded_target

    with factory() as database:
        first = ensure_lap_telemetry(
            database,
            session_id=session_id,
            session_entry_id=entry_id,
            lap_number=7,
        )
    with factory() as database:
        second = ensure_lap_telemetry(
            database,
            session_id=session_id,
            session_entry_id=entry_id,
            lap_number=7,
        )
        count = database.scalar(
            select(func.count(LapTelemetryIngestion.lap_id)).where(
                LapTelemetryIngestion.lap_id == lap_id
            )
        )

    assert first.action == "queued"
    assert second.action == "reused"
    assert second.source_snapshot_completed_at == marker
    assert count == 1


def test_claim_persistence_and_stale_replacement_are_snapshot_bound(
    seeded_target,
) -> None:
    factory, session_id, entry_id, lap_id, _ = seeded_target
    with factory() as database:
        ensure_lap_telemetry(
            database,
            session_id=session_id,
            session_entry_id=entry_id,
            lap_number=7,
        )
    with factory() as database:
        claim = claim_next_telemetry_lap(database)
    assert claim is not None
    assert claim.request.driver_identifier == "4"
    assert claim.request.lap_number == 7

    with factory() as database:
        summary = replace_lap_telemetry(
            database,
            claim=claim,
            samples=(_sample(0), _sample(1)),
        )
    assert summary.sample_count == 2

    replacement_snapshot = datetime.now(UTC) + timedelta(seconds=1)
    with factory.begin() as database:
        ingestion = database.get(SessionIngestion, session_id)
        assert ingestion is not None
        ingestion.completed_at = replacement_snapshot
    with factory() as database:
        command = ensure_lap_telemetry(
            database,
            session_id=session_id,
            session_entry_id=entry_id,
            lap_number=7,
        )
    assert command.action == "queued"
    with factory() as database:
        replacement_claim = claim_next_telemetry_lap(database)
    assert replacement_claim is not None
    with factory() as database:
        replace_lap_telemetry(
            database,
            claim=replacement_claim,
            samples=(_sample(0),),
        )
    with factory() as database:
        indices = database.scalars(
            select(LapTelemetrySample.sample_index)
            .where(LapTelemetrySample.lap_id == lap_id)
            .order_by(LapTelemetrySample.sample_index)
        ).all()
    assert indices == [0]


def test_attempt_token_fences_lost_claim_and_expired_lease_recovers(
    seeded_target,
) -> None:
    factory, session_id, entry_id, lap_id, _ = seeded_target
    with factory() as database:
        ensure_lap_telemetry(
            database,
            session_id=session_id,
            session_entry_id=entry_id,
            lap_number=7,
        )
    with factory() as database:
        claim = claim_next_telemetry_lap(database)
    assert claim is not None

    with factory.begin() as database:
        state = database.get(LapTelemetryIngestion, lap_id)
        assert state is not None
        state.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    with factory() as database:
        recovered = recover_stale_telemetry_leases(
            database,
            settings=BackfillRuntimeSettings(),
            jitter_fraction_factory=lambda: 0.0,
        )
    assert recovered == 1
    with factory() as database:
        with pytest.raises(TelemetryClaimOwnershipError):
            heartbeat_telemetry_lap(database, claim=claim)
