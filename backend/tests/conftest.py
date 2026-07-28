from collections.abc import Iterator
from types import ModuleType

import pytest
from sqlalchemy import create_engine, text

from app.db.engine import sqlalchemy_database_url


@pytest.fixture(autouse=True)
def reset_fastf1_request_gate(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep database-backed tests isolated from the global archive request gate."""

    module = request.module
    test_database_url = (
        getattr(module, "TEST_DATABASE_URL", None)
        if isinstance(module, ModuleType)
        else None
    )
    if test_database_url is None:
        yield
        return

    engine = create_engine(sqlalchemy_database_url(test_database_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE upstream_request_gates
                    SET next_request_at = clock_timestamp(),
                        reason = 'pacing'
                    WHERE source = 'fastf1_archive'
                    """
                )
            )
        yield
    finally:
        engine.dispose()
