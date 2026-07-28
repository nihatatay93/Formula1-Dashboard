from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import sqlalchemy_database_url


def test_metadata_contains_all_migrated_tables() -> None:
    assert set(Base.metadata.tables) == {
        "backfill_job_sessions",
        "backfill_jobs",
        "drivers",
        "deferred_season_events",
        "events",
        "laps",
        "lap_telemetry_ingestions",
        "lap_telemetry_samples",
        "seasons",
        "session_entries",
        "session_ingestions",
        "session_results",
        "sessions",
        "upstream_request_gates",
        "upstream_request_events",
    }


def test_active_backfill_job_index_is_partial_and_unique() -> None:
    table = Base.metadata.tables["backfill_jobs"]
    index = next(
        candidate
        for candidate in table.indexes
        if candidate.name == "uq_backfill_jobs_active_season"
    )

    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "status IN ('pending', 'running')"
    )


def test_all_check_constraints_are_named() -> None:
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert constraint.name is not None


def test_session_entry_identity_indexes_are_partial_and_unique() -> None:
    table = Base.metadata.tables["session_entries"]
    indexes = {index.name: index for index in table.indexes}

    number_index = indexes["uq_session_entries_session_racing_number"]
    assert number_index.unique is True
    assert str(number_index.dialect_options["postgresql"]["where"]) == (
        "racing_number IS NOT NULL"
    )

    driver_index = indexes["uq_session_entries_session_driver"]
    assert driver_index.unique is True
    assert str(driver_index.dialect_options["postgresql"]["where"]) == (
        "driver_id IS NOT NULL"
    )


def test_sporting_data_natural_keys_are_declared() -> None:
    expected_unique_columns = {
        "session_entries": {("session_id", "entry_key")},
        "laps": {("session_entry_id", "lap_number")},
    }

    for table_name, expected in expected_unique_columns.items():
        constraints = {
            tuple(column.name for column in constraint.columns)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert expected <= constraints


def test_unresolved_session_entry_driver_is_nullable() -> None:
    driver_id = Base.metadata.tables["session_entries"].c.driver_id

    assert driver_id.nullable is True


def test_unknown_historical_personal_best_flag_is_nullable() -> None:
    is_personal_best = Base.metadata.tables["laps"].c.is_personal_best

    assert is_personal_best.nullable is True


def test_calendar_discovery_markers_and_indexes_are_declared() -> None:
    events = Base.metadata.tables["events"]
    sessions = Base.metadata.tables["sessions"]

    assert events.c.last_discovered_at.nullable is True
    assert sessions.c.last_discovered_at.nullable is True
    assert "ix_events_season_year_last_discovered_at" in {
        index.name for index in events.indexes
    }
    assert "ix_sessions_event_id_last_discovered_at" in {
        index.name for index in sessions.indexes
    }


def test_database_url_uses_explicit_psycopg_driver() -> None:
    url = sqlalchemy_database_url(
        "postgresql://formula1_dashboard@db:5432/formula1_dashboard"
    )

    assert url.drivername == "postgresql+psycopg"


def test_lap_telemetry_metadata_is_bounded_and_cascades_with_lap() -> None:
    ingestions = Base.metadata.tables["lap_telemetry_ingestions"]
    samples = Base.metadata.tables["lap_telemetry_samples"]

    assert ingestions.c.lap_id.primary_key is True
    assert samples.c.sample_index.nullable is False
    assert {
        tuple(column.name for column in constraint.columns)
        for constraint in samples.constraints
        if isinstance(constraint, UniqueConstraint)
    } >= {("lap_id", "sample_index")}
    assert next(iter(ingestions.c.lap_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(samples.c.lap_id.foreign_keys)).ondelete == "CASCADE"


def test_deferred_event_membership_is_snapshot_indexed() -> None:
    table = Base.metadata.tables["deferred_season_events"]

    assert [column.name for column in table.primary_key.columns] == [
        "season_year",
        "round_number",
    ]
    assert next(iter(table.c.season_year.foreign_keys)).ondelete == "CASCADE"
    assert (
        "ix_deferred_season_events_season_year_discovered_at_round_number"
        in {index.name for index in table.indexes}
    )
