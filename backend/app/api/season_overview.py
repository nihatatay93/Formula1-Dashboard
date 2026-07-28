from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.api.contracts import (
    ActiveJobSummary,
    ArchiveEligibility,
    LastError,
    SeasonCounts,
    SeasonCoverage,
    SeasonEvent,
    SeasonOverviewResponse,
    SeasonSession,
)
from app.api.contracts import (
    SessionIngestion as SessionIngestionContract,
)
from app.api.season_status import SeasonStatusFacts, derive_season_status
from app.db.models import (
    BackfillJob,
    Event,
    RaceSession,
    Season,
    SessionIngestion,
)
from app.ingestion.freshness_policy import (
    evaluate_archive_ingestion,
    evaluate_season_coverage,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings

SessionFactory = Callable[[], Session]

_ACTIVE_JOB_STATUSES = ("pending", "running")
_MINIMUM_SEASON_YEAR = 2018


class SeasonOverviewReadError(ValueError):
    """Raised when a season overview request violates the service contract."""


def read_season_overview(
    *,
    season_year: int,
    session_factory: SessionFactory,
    settings: BackfillRuntimeSettings | None = None,
) -> SeasonOverviewResponse:
    """Build one season overview without writes or upstream access."""

    _validate_season_year(season_year)
    runtime_settings = settings or BackfillRuntimeSettings()

    with session_factory() as database, database.begin():
        database.execute(
            text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
        )
        database_now = database.scalar(select(func.now()))
        if database_now is None:
            raise SeasonOverviewReadError(
                "database did not return its current timestamp"
            )

        season = database.get(Season, season_year)
        coverage = evaluate_season_coverage(
            season_year=season_year,
            coverage_valid_until=(
                season.coverage_valid_until if season is not None else None
            ),
            database_now=database_now,
            settings=runtime_settings,
        )
        active_job = database.scalar(
            select(BackfillJob)
            .where(
                BackfillJob.season_year == season_year,
                BackfillJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(BackfillJob.requested_at.desc(), BackfillJob.id)
            .limit(1)
        )

        event_rows = _latest_calendar_rows(
            database,
            season_year=season_year,
            coverage_checked_at=(
                season.coverage_checked_at if season is not None else None
            ),
        )

        events: list[SeasonEvent] = []
        status_counts: Counter[str] = Counter()
        data_available_count = 0
        archive_eligible_count = 0
        required_counts: Counter[str] = Counter()

        for event, session_rows in event_rows:
            response_sessions: list[SeasonSession] = []
            for race_session, ingestion in session_rows:
                eligibility = evaluate_archive_ingestion(
                    scheduled_end_at=race_session.scheduled_end_at,
                    completed_at=(
                        ingestion.completed_at
                        if ingestion is not None
                        else None
                    ),
                    database_now=database_now,
                    settings=runtime_settings,
                )
                data_available = (
                    ingestion is not None
                    and ingestion.completed_at is not None
                )

                if ingestion is not None:
                    status_counts[ingestion.status] += 1
                if data_available:
                    data_available_count += 1
                if eligibility.eligible:
                    archive_eligible_count += 1
                    _record_required_state(required_counts, ingestion)

                response_sessions.append(
                    SeasonSession(
                        id=race_session.id,
                        session_key=race_session.session_key,
                        session_name=race_session.session_name,
                        scheduled_start_at=race_session.scheduled_start_at,
                        scheduled_end_at=race_session.scheduled_end_at,
                        archive_eligibility=ArchiveEligibility(
                            eligible=eligibility.eligible,
                            reason=eligibility.reason.value,
                            eligible_at=eligibility.eligible_at,
                        ),
                        ingestion=_ingestion_contract(ingestion),
                        data_available=data_available,
                    )
                )

            events.append(
                SeasonEvent(
                    id=event.id,
                    round_number=event.round_number,
                    official_name=event.official_name,
                    event_name=event.event_name,
                    country=event.country,
                    location=event.location,
                    event_format=event.event_format,
                    starts_at=event.starts_at,
                    ends_at=event.ends_at,
                    sessions=tuple(response_sessions),
                )
            )

        session_count = sum(len(event.sessions) for event in events)
        season_status = derive_season_status(
            SeasonStatusFacts(
                data_available_count=data_available_count,
                required_pending_count=required_counts["pending"],
                required_running_count=required_counts["running"],
                required_failed_count=required_counts["failed"],
                required_refresh_count=required_counts["refresh"],
                has_active_job=active_job is not None,
                coverage_is_stale=coverage.refresh_required,
            )
        )

        return SeasonOverviewResponse(
            year=season_year,
            status=season_status,
            coverage=SeasonCoverage(
                checked_at=(
                    season.coverage_checked_at
                    if season is not None
                    else None
                ),
                valid_until=(
                    season.coverage_valid_until
                    if season is not None
                    else None
                ),
                is_stale=coverage.refresh_required,
            ),
            counts=SeasonCounts(
                events=len(events),
                sessions=session_count,
                archive_eligible=archive_eligible_count,
                data_available=data_available_count,
                pending=status_counts["pending"],
                running=status_counts["running"],
                completed=status_counts["completed"],
                failed=status_counts["failed"],
            ),
            active_job=(
                ActiveJobSummary(id=active_job.id, status=active_job.status)
                if active_job is not None
                else None
            ),
            events=tuple(events),
        )


def _latest_calendar_rows(
    database: Session,
    *,
    season_year: int,
    coverage_checked_at: datetime | None,
) -> list[tuple[Event, list[tuple[RaceSession, SessionIngestion | None]]]]:
    if coverage_checked_at is None:
        return []

    rows = database.execute(
        select(Event, RaceSession, SessionIngestion)
        .outerjoin(
            RaceSession,
            and_(
                RaceSession.event_id == Event.id,
                RaceSession.last_discovered_at == coverage_checked_at,
            ),
        )
        .outerjoin(
            SessionIngestion,
            SessionIngestion.session_id == RaceSession.id,
        )
        .where(
            Event.season_year == season_year,
            Event.last_discovered_at == coverage_checked_at,
        )
        .order_by(
            Event.round_number,
            Event.id,
            RaceSession.scheduled_start_at.asc().nulls_last(),
            RaceSession.id.asc().nulls_last(),
        )
    ).all()

    grouped: list[
        tuple[Event, list[tuple[RaceSession, SessionIngestion | None]]]
    ] = []
    current_event_id: int | None = None
    current_sessions: list[tuple[RaceSession, SessionIngestion | None]]

    for event, race_session, ingestion in rows:
        if event.id != current_event_id:
            current_sessions = []
            grouped.append((event, current_sessions))
            current_event_id = event.id
        if race_session is not None:
            current_sessions.append((race_session, ingestion))

    return grouped


def _record_required_state(
    required_counts: Counter[str],
    ingestion: SessionIngestion | None,
) -> None:
    if ingestion is None or ingestion.status == "completed":
        required_counts["refresh"] += 1
    elif ingestion.status in {"pending", "running", "failed"}:
        required_counts[ingestion.status] += 1


def _ingestion_contract(
    ingestion: SessionIngestion | None,
) -> SessionIngestionContract | None:
    if ingestion is None:
        return None
    return SessionIngestionContract(
        status=ingestion.status,
        record_state=ingestion.record_state,
        attempt_count=ingestion.attempt_count,
        completed_at=ingestion.completed_at,
        next_retry_at=ingestion.next_retry_at,
        last_error=_last_error(ingestion),
    )


def _last_error(ingestion: SessionIngestion) -> LastError | None:
    if (
        ingestion.last_error_code is None
        or ingestion.last_error_message is None
    ):
        return None
    return LastError(
        code=ingestion.last_error_code,
        message=ingestion.last_error_message,
    )


def _validate_season_year(season_year: object) -> None:
    if (
        isinstance(season_year, bool)
        or not isinstance(season_year, int)
        or season_year < _MINIMUM_SEASON_YEAR
    ):
        raise SeasonOverviewReadError(
            "season_year must be an integer greater than or equal to 2018"
        )
