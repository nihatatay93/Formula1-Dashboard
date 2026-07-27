from sqlalchemy import CheckConstraint

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import sqlalchemy_database_url


def test_control_plane_metadata_contains_only_revision_one_tables() -> None:
    assert set(Base.metadata.tables) == {
        "backfill_job_sessions",
        "backfill_jobs",
        "events",
        "seasons",
        "session_ingestions",
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


def test_control_plane_check_constraints_are_named() -> None:
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert constraint.name is not None


def test_database_url_uses_explicit_psycopg_driver() -> None:
    url = sqlalchemy_database_url(
        "postgresql://formula1_dashboard@db:5432/formula1_dashboard"
    )

    assert url.drivername == "postgresql+psycopg"
