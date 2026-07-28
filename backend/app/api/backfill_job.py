from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.contracts import (
    BackfillExecution,
    BackfillExecutionPhase,
    BackfillJobResponse,
    BackfillSessionReference,
    JobProgress,
    LastError,
)
from app.api.contracts import (
    BackfillJobSession as BackfillJobSessionContract,
)
from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    Event,
    RaceSession,
    UpstreamRequestGate,
)

FASTF1_GATE_SOURCE = "fastf1_archive"

SessionFactory = Callable[[], Session]


class BackfillJobReadError(ValueError):
    """Raised when a backfill-job read violates the service contract."""


class BackfillJobNotFoundError(BackfillJobReadError):
    """Raised when the requested backfill job does not exist."""


def read_backfill_job(
    *,
    job_id: uuid.UUID,
    session_factory: SessionFactory,
) -> BackfillJobResponse:
    """Read one job and its child progress without aggregation or writes."""

    _validate_job_id(job_id)

    with session_factory() as database, database.begin():
        database.execute(
            text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
        )
        job = database.get(BackfillJob, job_id)
        if job is None:
            raise BackfillJobNotFoundError(
                f"backfill job {job_id} does not exist"
            )

        rows = database.execute(
            select(BackfillJobSession, RaceSession, Event)
            .join(
                RaceSession,
                RaceSession.id == BackfillJobSession.session_id,
            )
            .join(Event, Event.id == RaceSession.event_id)
            .where(BackfillJobSession.job_id == job_id)
            .order_by(
                Event.round_number,
                Event.id,
                RaceSession.scheduled_start_at.asc().nulls_last(),
                RaceSession.id,
            )
        ).all()
        observed_at = _database_now(database)
        request_gate = database.get(
            UpstreamRequestGate,
            FASTF1_GATE_SOURCE,
        )

        counts: Counter[str] = Counter()
        response_sessions: list[BackfillJobSessionContract] = []
        for job_session, race_session, event in rows:
            counts[job_session.status] += 1
            response_sessions.append(
                BackfillJobSessionContract(
                    session_id=race_session.id,
                    round_number=event.round_number,
                    event_name=event.event_name,
                    session_key=race_session.session_key,
                    session_name=race_session.session_name,
                    status=job_session.status,
                    attempt_count=job_session.attempt_count,
                    queued_at=job_session.queued_at,
                    started_at=job_session.started_at,
                    heartbeat_at=job_session.heartbeat_at,
                    next_retry_at=job_session.next_retry_at,
                    completed_at=job_session.completed_at,
                    last_error=_last_error(job_session),
                )
            )

        completed_count = counts["completed"]
        failed_count = counts["failed"]
        execution = _execution_snapshot(
            job=job,
            rows=rows,
            observed_at=observed_at,
            request_gate=request_gate,
        )
        return BackfillJobResponse(
            id=job.id,
            season_year=job.season_year,
            status=job.status,
            request_reason=job.request_reason,
            requested_at=job.requested_at,
            started_at=job.started_at,
            heartbeat_at=job.heartbeat_at,
            completed_at=job.completed_at,
            last_error=_last_error(job),
            progress=JobProgress(
                total=len(response_sessions),
                pending=counts["pending"],
                running=counts["running"],
                completed=completed_count,
                failed=failed_count,
                terminal=completed_count + failed_count,
            ),
            execution=execution,
            sessions=tuple(response_sessions),
        )


def _execution_snapshot(
    *,
    job: BackfillJob,
    rows: list[tuple[BackfillJobSession, RaceSession, Event]],
    observed_at: datetime,
    request_gate: UpstreamRequestGate | None,
) -> BackfillExecution:
    running = [
        row for row in rows if row[0].status == "running"
    ]
    pending = [
        row for row in rows if row[0].status == "pending"
    ]
    completed = [
        row
        for row in rows
        if row[0].status == "completed"
        and row[0].completed_at is not None
    ]

    current_row = min(
        running,
        key=lambda row: (
            row[0].started_at or observed_at,
            row[0].session_id,
        ),
        default=None,
    )
    next_row = min(
        pending,
        key=lambda row: (
            row[0].next_retry_at is not None,
            (
                row[0].next_retry_at.timestamp()
                if row[0].next_retry_at is not None
                else 0
            ),
            row[0].queued_at,
            row[0].session_id,
        ),
        default=None,
    )
    last_completed_row = max(
        completed,
        key=lambda row: (
            row[0].completed_at,
            row[0].session_id,
        ),
        default=None,
    )

    next_action_at = None
    if job.status in {"completed", "failed"}:
        phase = BackfillExecutionPhase.TERMINAL
    elif current_row is not None:
        phase = BackfillExecutionPhase.FETCHING
    elif next_row is None:
        phase = BackfillExecutionPhase.IDLE
    else:
        candidate_times = []
        retry_at = next_row[0].next_retry_at
        if retry_at is not None and retry_at > observed_at:
            candidate_times.append(retry_at)
        gate_waiting = (
            request_gate is not None
            and request_gate.next_request_at > observed_at
        )
        if gate_waiting:
            candidate_times.append(request_gate.next_request_at)
        next_action_at = max(candidate_times, default=None)

        if gate_waiting and request_gate is not None:
            phase = {
                "rate_limit": BackfillExecutionPhase.RATE_LIMIT_COOLDOWN,
                "budget": BackfillExecutionPhase.REQUEST_BUDGET_COOLDOWN,
            }.get(
                request_gate.reason,
                BackfillExecutionPhase.PACING,
            )
        elif retry_at is not None and retry_at > observed_at:
            phase = BackfillExecutionPhase.RETRY_BACKOFF
        else:
            phase = BackfillExecutionPhase.READY

    return BackfillExecution(
        observed_at=observed_at,
        phase=phase,
        current_session=_session_reference(current_row),
        next_session=_session_reference(next_row),
        last_completed_session=_session_reference(last_completed_row),
        next_action_at=next_action_at,
    )


def _session_reference(
    row: tuple[BackfillJobSession, RaceSession, Event] | None,
) -> BackfillSessionReference | None:
    if row is None:
        return None
    _, race_session, event = row
    return BackfillSessionReference(
        session_id=race_session.id,
        round_number=event.round_number,
        event_name=event.event_name,
        session_name=race_session.session_name,
    )


def _last_error(
    row: BackfillJob | BackfillJobSession,
) -> LastError | None:
    if row.last_error_code is None or row.last_error_message is None:
        return None
    return LastError(
        code=row.last_error_code,
        message=row.last_error_message,
    )


def _validate_job_id(job_id: object) -> None:
    if not isinstance(job_id, uuid.UUID):
        raise BackfillJobReadError("job_id must be a UUID")


def _database_now(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise BackfillJobReadError(
            "PostgreSQL did not return a timestamp"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise BackfillJobReadError(
            "PostgreSQL returned a timestamp without a timezone"
        )
    return value.astimezone(UTC)
