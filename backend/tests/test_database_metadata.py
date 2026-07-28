from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import sqlalchemy_database_url


def test_metadata_contains_all_migrated_tables() -> None:
    assert set(Base.metadata.tables) == {
        "backfill_job_sessions",
        "backfill_jobs",
        "drivers",
        "events",
        "laps",
        "seasons",
        "session_entries",
        "session_ingestions",
        "session_results",
        "sessions",
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
