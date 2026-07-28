from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.contracts import (
    BackfillJobResponse,
    JobProgress,
    LastError,
)
from app.api.contracts import (
    BackfillJobSession as BackfillJobSessionContract,
)
from app.db.models import BackfillJob, BackfillJobSession, Event, RaceSession

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
            sessions=tuple(response_sessions),
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
