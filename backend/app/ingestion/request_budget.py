from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import UpstreamRequestEvent, UpstreamRequestGate
from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings

SessionFactory = Callable[[], Session]

FASTF1_GATE_SOURCE = "fastf1_archive"
FASTF1_EVENT_SOURCE = "fastf1"
EVENT_RETENTION = timedelta(hours=24)


class FastF1RequestBudgetError(RuntimeError):
    """Base error for persistent FastF1 request-budget coordination."""


@dataclass(frozen=True, slots=True)
class FastF1RequestBudgetSnapshot:
    observed_at: datetime
    observed_requests: int
    archive_requests: int
    schedule_requests: int
    telemetry_requests: int
    library_limit: int
    operational_ceiling: int
    warning_threshold: int
    remaining_before_pause: int
    next_capacity_at: datetime | None
    cooldown_until: datetime | None
    cooldown_reason: str | None
    status: str


class FastF1RequestBudget:
    """Reserve real outbound FastF1 requests in one rolling PostgreSQL budget."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        operation: str,
        settings: BackfillRuntimeSettings | None = None,
    ) -> None:
        if operation not in {"archive", "schedule", "telemetry"}:
            raise ValueError(
                "operation must be archive, schedule, or telemetry"
            )
        self._session_factory = session_factory
        self._operation = operation
        self._settings = settings or BackfillRuntimeSettings()

    def reserve(self) -> None:
        exhausted_retry_at: datetime | None = None
        with self._session_factory() as database, database.begin():
            gate = _gate_for_update(database)
            observed_at = _database_now(database)

            if (
                gate.reason in {"rate_limit", "budget"}
                and gate.next_request_at > observed_at
            ):
                exhausted_retry_at = gate.next_request_at
            else:
                cutoff = observed_at - timedelta(
                    seconds=self._settings.fastf1_request_window_seconds
                )
                observed_requests = _request_count(
                    database,
                    cutoff=cutoff,
                )
                if (
                    observed_requests
                    >= self._settings.fastf1_request_operational_ceiling
                ):
                    exhausted_retry_at = _next_capacity_at(
                        database,
                        cutoff=cutoff,
                        window=timedelta(
                            seconds=(
                                self._settings.fastf1_request_window_seconds
                            )
                        ),
                    )
                    gate.next_request_at = max(
                        gate.next_request_at,
                        exhausted_retry_at,
                    )
                    gate.reason = "budget"
                else:
                    database.add(
                        UpstreamRequestEvent(
                            source=FASTF1_EVENT_SOURCE,
                            operation=self._operation,
                            requested_at=observed_at,
                        )
                    )
                    database.execute(
                        delete(UpstreamRequestEvent).where(
                            UpstreamRequestEvent.requested_at
                            < observed_at - EVENT_RETENTION
                        )
                    )
                    if gate.reason == "budget":
                        gate.next_request_at = observed_at
                        gate.reason = "pacing"

        if exhausted_retry_at is not None:
            raise FastF1RequestBudgetExhaustedError(
                retry_at=exhausted_retry_at
            )


def read_fastf1_request_budget(
    *,
    session_factory: SessionFactory,
    settings: BackfillRuntimeSettings | None = None,
) -> FastF1RequestBudgetSnapshot:
    runtime_settings = settings or BackfillRuntimeSettings()
    with session_factory() as database, database.begin():
        observed_at = _database_now(database)
        cutoff = observed_at - timedelta(
            seconds=runtime_settings.fastf1_request_window_seconds
        )
        counts = dict(
            database.execute(
                select(
                    UpstreamRequestEvent.operation,
                    func.count(UpstreamRequestEvent.id),
                )
                .where(UpstreamRequestEvent.requested_at >= cutoff)
                .group_by(UpstreamRequestEvent.operation)
            ).all()
        )
        observed_requests = sum(int(value) for value in counts.values())
        next_capacity_at = (
            _next_capacity_at(
                database,
                cutoff=cutoff,
                window=timedelta(
                    seconds=runtime_settings.fastf1_request_window_seconds
                ),
            )
            if observed_requests
            >= runtime_settings.fastf1_request_operational_ceiling
            else None
        )
        gate = database.get(UpstreamRequestGate, FASTF1_GATE_SOURCE)
        cooldown_until = None
        cooldown_reason = None
        if (
            gate is not None
            and gate.reason in {"rate_limit", "budget"}
            and gate.next_request_at > observed_at
        ):
            cooldown_until = gate.next_request_at
            cooldown_reason = gate.reason

        if cooldown_reason == "rate_limit":
            status = "rate_limited"
        elif cooldown_until is not None or observed_requests >= (
            runtime_settings.fastf1_request_operational_ceiling
        ):
            status = "paused"
        elif observed_requests >= (
            runtime_settings.fastf1_request_warning_threshold
        ):
            status = "warning"
        else:
            status = "available"

        return FastF1RequestBudgetSnapshot(
            observed_at=observed_at,
            observed_requests=observed_requests,
            archive_requests=int(counts.get("archive", 0)),
            schedule_requests=int(counts.get("schedule", 0)),
            telemetry_requests=int(counts.get("telemetry", 0)),
            library_limit=runtime_settings.fastf1_request_library_limit,
            operational_ceiling=(
                runtime_settings.fastf1_request_operational_ceiling
            ),
            warning_threshold=(
                runtime_settings.fastf1_request_warning_threshold
            ),
            remaining_before_pause=max(
                0,
                runtime_settings.fastf1_request_operational_ceiling
                - observed_requests,
            ),
            next_capacity_at=next_capacity_at,
            cooldown_until=cooldown_until,
            cooldown_reason=cooldown_reason,
            status=status,
        )


def _gate_for_update(database: Session) -> UpstreamRequestGate:
    gate = database.scalar(
        select(UpstreamRequestGate)
        .where(UpstreamRequestGate.source == FASTF1_GATE_SOURCE)
        .with_for_update()
    )
    if gate is None:
        raise FastF1RequestBudgetError("FastF1 request gate is missing")
    return gate


def _request_count(database: Session, *, cutoff: datetime) -> int:
    return int(
        database.scalar(
            select(func.count(UpstreamRequestEvent.id)).where(
                UpstreamRequestEvent.requested_at >= cutoff
            )
        )
        or 0
    )


def _next_capacity_at(
    database: Session,
    *,
    cutoff: datetime,
    window: timedelta,
) -> datetime:
    oldest = database.scalar(
        select(func.min(UpstreamRequestEvent.requested_at)).where(
            UpstreamRequestEvent.requested_at >= cutoff
        )
    )
    if not isinstance(oldest, datetime):
        raise FastF1RequestBudgetError(
            "FastF1 request budget has no release timestamp"
        )
    return oldest.astimezone(UTC) + window


def _database_now(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise FastF1RequestBudgetError(
            "PostgreSQL did not return a timestamp"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise FastF1RequestBudgetError(
            "PostgreSQL returned a timestamp without a timezone"
        )
    return value.astimezone(UTC)
