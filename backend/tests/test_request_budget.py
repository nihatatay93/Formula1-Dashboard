import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.ingestion.request_budget import (
    FastF1RequestBudget,
    read_fastf1_request_budget,
)
from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def request_budget_factory() -> Iterator[sessionmaker[Session]]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for request-budget tests")

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM upstream_request_events"))
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
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM upstream_request_events"))
        engine.dispose()


def settings() -> BackfillRuntimeSettings:
    return BackfillRuntimeSettings(
        fastf1_request_warning_threshold=2,
        fastf1_request_operational_ceiling=3,
        fastf1_request_library_limit=4,
    )


def test_reserves_real_requests_across_operations_and_pauses_at_ceiling(
    request_budget_factory: sessionmaker[Session],
) -> None:
    archive = FastF1RequestBudget(
        session_factory=request_budget_factory,
        operation="archive",
        settings=settings(),
    )
    schedule = FastF1RequestBudget(
        session_factory=request_budget_factory,
        operation="schedule",
        settings=settings(),
    )
    telemetry = FastF1RequestBudget(
        session_factory=request_budget_factory,
        operation="telemetry",
        settings=settings(),
    )

    archive.reserve()
    schedule.reserve()
    telemetry.reserve()

    with pytest.raises(FastF1RequestBudgetExhaustedError) as error:
        schedule.reserve()

    snapshot = read_fastf1_request_budget(
        session_factory=request_budget_factory,
        settings=settings(),
    )
    assert snapshot.observed_requests == 3
    assert snapshot.archive_requests == 1
    assert snapshot.schedule_requests == 1
    assert snapshot.telemetry_requests == 1
    assert snapshot.remaining_before_pause == 0
    assert snapshot.status == "paused"
    assert snapshot.cooldown_reason == "budget"
    assert snapshot.cooldown_until == error.value.retry_at
    assert snapshot.next_capacity_at == error.value.retry_at


def test_snapshot_warns_before_operational_ceiling(
    request_budget_factory: sessionmaker[Session],
) -> None:
    budget = FastF1RequestBudget(
        session_factory=request_budget_factory,
        operation="archive",
        settings=settings(),
    )
    budget.reserve()
    budget.reserve()

    snapshot = read_fastf1_request_budget(
        session_factory=request_budget_factory,
        settings=settings(),
    )

    assert snapshot.status == "warning"
    assert snapshot.remaining_before_pause == 1
    assert snapshot.cooldown_until is None
