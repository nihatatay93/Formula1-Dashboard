from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    DeferredSeasonEvent,
    Event,
    RaceSession,
    Season,
    SessionIngestion,
)
from app.ingestion.fastf1_loader import MINIMUM_ARCHIVE_YEAR
from app.ingestion.fastf1_normalization import ARCHIVE_SOURCE
from app.ingestion.fastf1_schedule import (
    DeferredFutureEvent,
    FastF1ScheduleLoaderProtocol,
    NormalizedSeasonSchedule,
)
from app.ingestion.freshness_policy import (
    CoverageRefreshReason,
    SeasonCoverageEligibility,
    evaluate_archive_ingestion,
    evaluate_season_coverage,
)
from app.ingestion.runtime_policy import BackfillRuntimeSettings

SessionFactory = Callable[[], Session]

_SEASON_BACKFILL_LOCK_NAMESPACE = 1_179_729_730


class SeasonBackfillError(RuntimeError):
    """Base error for season discovery and backfill planning."""


class SeasonBackfillSourceConflictError(SeasonBackfillError):
    """Raised when archive discovery would overwrite another source."""


class SeasonBackfillSnapshotError(SeasonBackfillError):
    """Raised when a loaded schedule does not match the requested season."""


@dataclass(frozen=True, slots=True)
class SeasonBackfillPlan:
    season_year: int
    coverage_reason: CoverageRefreshReason
    coverage_refreshed: bool
    coverage_checked_at: datetime | None
    coverage_valid_until: datetime | None
    job_id: uuid.UUID | None
    job_status: str | None
    job_created: bool
    eligible_session_ids: tuple[int, ...]
    newly_queued_session_ids: tuple[int, ...]
    deferred_future_events: tuple[DeferredFutureEvent, ...] = ()


def ensure_season_backfill(
    *,
    season_year: int,
    session_factory: SessionFactory,
    schedule_loader: FastF1ScheduleLoaderProtocol,
    settings: BackfillRuntimeSettings | None = None,
) -> SeasonBackfillPlan:
    """Refresh stale schedule coverage and create or reuse one active job."""

    _validate_season_year(season_year)
    runtime_settings = settings or BackfillRuntimeSettings()
    loaded_schedule: NormalizedSeasonSchedule | None = None

    for _attempt in range(2):
        if loaded_schedule is None and _coverage_refresh_required(
            season_year=season_year,
            session_factory=session_factory,
            settings=runtime_settings,
        ):
            loaded_schedule = schedule_loader.load(season_year)
            _validate_loaded_schedule(loaded_schedule, season_year)

        with session_factory() as database, database.begin():
            _lock_season_backfill(database, season_year)
            database_now = _database_now(database)
            season = database.scalar(
                select(Season)
                .where(Season.year == season_year)
                .with_for_update()
            )
            coverage = evaluate_season_coverage(
                season_year=season_year,
                coverage_valid_until=(
                    season.coverage_valid_until
                    if season is not None
                    else None
                ),
                database_now=database_now,
                settings=runtime_settings,
            )

            if coverage.refresh_required and loaded_schedule is None:
                continue

            coverage_refreshed = False
            if coverage.refresh_required:
                assert loaded_schedule is not None
                season = _persist_schedule_snapshot(
                    database,
                    schedule=loaded_schedule,
                    discovered_at=database_now,
                    coverage=coverage,
                )
                coverage_refreshed = True

            assert season is not None
            return _create_or_reuse_job(
                database,
                season=season,
                coverage=coverage,
                coverage_refreshed=coverage_refreshed,
                database_now=database_now,
                settings=runtime_settings,
            )

    raise SeasonBackfillError(
        f"season {season_year} coverage changed repeatedly during planning"
    )


def _coverage_refresh_required(
    *,
    season_year: int,
    session_factory: SessionFactory,
    settings: BackfillRuntimeSettings,
) -> bool:
    with session_factory() as database, database.begin():
        database_now = _database_now(database)
        season = database.get(Season, season_year)
        return evaluate_season_coverage(
            season_year=season_year,
            coverage_valid_until=(
                season.coverage_valid_until
                if season is not None
                else None
            ),
            database_now=database_now,
            settings=settings,
        ).refresh_required


def _persist_schedule_snapshot(
    database: Session,
    *,
    schedule: NormalizedSeasonSchedule,
    discovered_at: datetime,
    coverage: SeasonCoverageEligibility,
) -> Season:
    season = database.get(Season, schedule.season_year)
    if season is None:
        season = Season(year=schedule.season_year)
        database.add(season)
        database.flush()

    existing_events = {
        event.round_number: event
        for event in database.scalars(
            select(Event)
            .where(Event.season_year == schedule.season_year)
            .order_by(Event.round_number)
            .with_for_update()
        ).all()
    }
    existing_sessions = {
        (race_session.event_id, race_session.session_key): race_session
        for race_session in database.scalars(
            select(RaceSession)
            .join(Event, Event.id == RaceSession.event_id)
            .where(Event.season_year == schedule.season_year)
            .order_by(RaceSession.event_id, RaceSession.session_key)
            .with_for_update()
        ).all()
    }
    deferred_rounds: set[int] = set()

    for scheduled_event in schedule.events:
        event = existing_events.get(scheduled_event.round_number)
        if event is None:
            event = Event(
                season_year=schedule.season_year,
                round_number=scheduled_event.round_number,
                event_name=scheduled_event.event_name,
                source=ARCHIVE_SOURCE,
            )
            database.add(event)
            database.flush()
            existing_events[scheduled_event.round_number] = event
        elif event.source != ARCHIVE_SOURCE:
            raise SeasonBackfillSourceConflictError(
                f"season {schedule.season_year} round "
                f"{scheduled_event.round_number} belongs to another source"
            )

        event.official_name = scheduled_event.official_name
        event.event_name = scheduled_event.event_name
        event.country = scheduled_event.country
        event.location = scheduled_event.location
        event.event_format = scheduled_event.event_format
        event.starts_at = scheduled_event.starts_at
        event.ends_at = scheduled_event.ends_at
        event.last_discovered_at = discovered_at
        event.updated_at = discovered_at

        for scheduled_session in scheduled_event.sessions:
            key = (event.id, scheduled_session.session_key)
            race_session = existing_sessions.get(key)
            if race_session is None:
                race_session = RaceSession(
                    event_id=event.id,
                    session_key=scheduled_session.session_key,
                    session_name=scheduled_session.session_name,
                    source=ARCHIVE_SOURCE,
                )
                database.add(race_session)
                existing_sessions[key] = race_session
            elif race_session.source != ARCHIVE_SOURCE:
                raise SeasonBackfillSourceConflictError(
                    f"season {schedule.season_year} round "
                    f"{scheduled_event.round_number} session "
                    f"{scheduled_session.session_key!r} belongs to "
                    "another source"
                )

            race_session.session_name = scheduled_session.session_name
            race_session.scheduled_start_at = (
                scheduled_session.scheduled_start_at
            )
            race_session.scheduled_end_at = (
                scheduled_session.scheduled_end_at
            )
            race_session.last_discovered_at = discovered_at
            race_session.updated_at = discovered_at

    for deferred in schedule.deferred_future_events:
        state = database.get(
            DeferredSeasonEvent,
            (schedule.season_year, deferred.round_number),
        )
        if state is None:
            state = DeferredSeasonEvent(
                season_year=schedule.season_year,
                round_number=deferred.round_number,
                event_name=deferred.event_name,
                scheduled_start_at=deferred.scheduled_start_at,
                discovered_at=discovered_at,
            )
            database.add(state)
        else:
            state.event_name = deferred.event_name
            state.scheduled_start_at = deferred.scheduled_start_at
            state.discovered_at = discovered_at
            state.updated_at = discovered_at
        deferred_rounds.add(deferred.round_number)

    stale_deferred = delete(DeferredSeasonEvent).where(
        DeferredSeasonEvent.season_year == schedule.season_year
    )
    if deferred_rounds:
        stale_deferred = stale_deferred.where(
            DeferredSeasonEvent.round_number.not_in(deferred_rounds)
        )
    database.execute(stale_deferred)

    season.coverage_checked_at = discovered_at
    season.coverage_valid_until = (
        coverage.successful_refresh_valid_until
    )
    season.updated_at = discovered_at
    database.flush()
    return season


def _create_or_reuse_job(
    database: Session,
    *,
    season: Season,
    coverage: SeasonCoverageEligibility,
    coverage_refreshed: bool,
    database_now: datetime,
    settings: BackfillRuntimeSettings,
) -> SeasonBackfillPlan:
    current_sessions = database.execute(
        select(RaceSession, SessionIngestion)
        .join(Event, Event.id == RaceSession.event_id)
        .outerjoin(
            SessionIngestion,
            SessionIngestion.session_id == RaceSession.id,
        )
        .where(
            Event.season_year == season.year,
            Event.source == ARCHIVE_SOURCE,
            RaceSession.source == ARCHIVE_SOURCE,
            Event.last_discovered_at == season.coverage_checked_at,
            RaceSession.last_discovered_at == season.coverage_checked_at,
        )
        .order_by(
            Event.round_number,
            RaceSession.scheduled_start_at,
            RaceSession.id,
        )
    ).all()

    active_job = database.scalar(
        select(BackfillJob)
        .where(
            BackfillJob.season_year == season.year,
            BackfillJob.status.in_(("pending", "running")),
        )
        .with_for_update()
    )

    eligible_rows: list[tuple[RaceSession, SessionIngestion | None]] = []
    for race_session, ingestion in current_sessions:
        if (
            ingestion is not None
            and ingestion.source != ARCHIVE_SOURCE
        ):
            continue
        if (
            active_job is None
            and ingestion is not None
            and ingestion.status in {"pending", "running"}
        ):
            continue
        eligibility = evaluate_archive_ingestion(
            scheduled_end_at=race_session.scheduled_end_at,
            completed_at=(
                ingestion.completed_at
                if ingestion is not None
                else None
            ),
            database_now=database_now,
            settings=settings,
        )
        if eligibility.eligible:
            eligible_rows.append((race_session, ingestion))

    job_created = False
    if active_job is None and eligible_rows:
        active_job = BackfillJob(
            season_year=season.year,
            request_reason=_request_reason(
                coverage=coverage,
                eligible_rows=eligible_rows,
            ),
        )
        database.add(active_job)
        database.flush()
        job_created = True

    newly_queued: list[int] = []
    if active_job is not None:
        existing_session_ids = set(
            database.scalars(
                select(BackfillJobSession.session_id)
                .where(BackfillJobSession.job_id == active_job.id)
                .with_for_update()
            ).all()
        )
        for race_session, ingestion in eligible_rows:
            if race_session.id in existing_session_ids:
                continue
            if (
                ingestion is not None
                and ingestion.status in {"pending", "running"}
            ):
                continue
            database.add(
                BackfillJobSession(
                    job_id=active_job.id,
                    session_id=race_session.id,
                )
            )
            newly_queued.append(race_session.id)

    database.flush()
    deferred_future_events = tuple(
        DeferredFutureEvent(
            round_number=state.round_number,
            event_name=state.event_name,
            scheduled_start_at=state.scheduled_start_at,
        )
        for state in database.scalars(
            select(DeferredSeasonEvent)
            .where(
                DeferredSeasonEvent.season_year == season.year,
                DeferredSeasonEvent.discovered_at
                == season.coverage_checked_at,
            )
            .order_by(
                DeferredSeasonEvent.round_number,
                DeferredSeasonEvent.scheduled_start_at,
            )
        )
    )
    return SeasonBackfillPlan(
        season_year=season.year,
        coverage_reason=coverage.reason,
        coverage_refreshed=coverage_refreshed,
        coverage_checked_at=season.coverage_checked_at,
        coverage_valid_until=season.coverage_valid_until,
        job_id=active_job.id if active_job is not None else None,
        job_status=(
            active_job.status
            if active_job is not None
            else None
        ),
        job_created=job_created,
        eligible_session_ids=tuple(
            race_session.id
            for race_session, _ingestion in eligible_rows
        ),
        newly_queued_session_ids=tuple(newly_queued),
        deferred_future_events=deferred_future_events,
    )


def _request_reason(
    *,
    coverage: SeasonCoverageEligibility,
    eligible_rows: list[tuple[RaceSession, SessionIngestion | None]],
) -> str:
    if coverage.reason is CoverageRefreshReason.MISSING:
        return "missing"
    if coverage.reason is CoverageRefreshReason.STALE:
        return "stale"
    if any(
        ingestion is not None and ingestion.completed_at is not None
        for _race_session, ingestion in eligible_rows
    ):
        return "stale"
    return "partial"


def _validate_loaded_schedule(
    schedule: NormalizedSeasonSchedule,
    season_year: int,
) -> None:
    if schedule.season_year != season_year:
        raise SeasonBackfillSnapshotError(
            f"loaded schedule year {schedule.season_year} does not match "
            f"requested year {season_year}"
        )


def _validate_season_year(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MINIMUM_ARCHIVE_YEAR
        or value > 32767
    ):
        raise SeasonBackfillError(
            f"season_year must be between {MINIMUM_ARCHIVE_YEAR} and 32767"
        )


def _lock_season_backfill(
    database: Session,
    season_year: int,
) -> None:
    database.execute(
        select(
            func.pg_advisory_xact_lock(
                _SEASON_BACKFILL_LOCK_NAMESPACE,
                season_year,
            )
        )
    )


def _database_now(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise SeasonBackfillError(
            "PostgreSQL did not return a timestamp"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise SeasonBackfillError(
            "PostgreSQL returned a timestamp without a timezone"
        )
    return value.astimezone(UTC)
