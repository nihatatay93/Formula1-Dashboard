from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.contracts import (
    LapTelemetryIngestionState,
    LapTelemetryPage,
    LapTelemetryResponse,
    LapTelemetrySampleData,
    LapTelemetrySnapshot,
    LastError,
)
from app.db.models import (
    Lap,
    LapTelemetryIngestion,
    LapTelemetrySample,
    RaceSession,
    SessionEntry,
    SessionIngestion,
)
from app.ingestion.telemetry_ingestion import (
    TelemetryDataUnavailableError,
    TelemetryTargetNotFoundError,
)

SessionFactory = Callable[[], Session]
_READ_ONLY_TRANSACTION = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)


class TelemetryNotRequestedError(ValueError):
    """Raised when no persistent telemetry state exists for a valid lap."""


def read_lap_telemetry(
    *,
    session_id: int,
    session_entry_id: int,
    lap_number: int,
    after_sample: int | None,
    limit: int,
    session_factory: SessionFactory,
) -> LapTelemetryResponse:
    with session_factory() as database, database.begin():
        database.execute(text(_READ_ONLY_TRANSACTION))
        row = database.execute(
            select(Lap, SessionIngestion, LapTelemetryIngestion)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .outerjoin(
                SessionIngestion,
                SessionIngestion.session_id == RaceSession.id,
            )
            .outerjoin(
                LapTelemetryIngestion,
                LapTelemetryIngestion.lap_id == Lap.id,
            )
            .where(
                RaceSession.id == session_id,
                SessionEntry.id == session_entry_id,
                Lap.lap_number == lap_number,
            )
        ).one_or_none()
        if row is None:
            raise TelemetryTargetNotFoundError(
                "the requested session entry lap does not exist"
            )
        lap, sporting, ingestion = row
        if (
            sporting is None
            or sporting.status != "completed"
            or sporting.completed_at is None
        ):
            raise TelemetryDataUnavailableError(
                "the owning historical snapshot is not completed"
            )
        if ingestion is None:
            raise TelemetryNotRequestedError(
                "telemetry has not been requested for this lap"
            )

        compatible = (
            ingestion.source_snapshot_completed_at == sporting.completed_at
        )
        available = (
            compatible
            and ingestion.status == "completed"
            and ingestion.sample_count > 0
        )
        samples: list[LapTelemetrySample] = []
        if available:
            statement = select(LapTelemetrySample).where(
                LapTelemetrySample.lap_id == lap.id
            )
            if after_sample is not None:
                statement = statement.where(
                    LapTelemetrySample.sample_index > after_sample
                )
            samples = list(
                database.scalars(
                    statement.order_by(
                        LapTelemetrySample.sample_index
                    ).limit(limit + 1)
                )
            )
        has_more = len(samples) > limit
        page_rows = samples[:limit]
        return LapTelemetryResponse(
            session_id=session_id,
            session_entry_id=session_entry_id,
            lap_id=lap.id,
            lap_number=lap.lap_number,
            data_available=available,
            snapshot=LapTelemetrySnapshot(
                compatible=compatible,
                source_snapshot_completed_at=(
                    ingestion.source_snapshot_completed_at
                ),
                current_snapshot_completed_at=sporting.completed_at,
            ),
            ingestion=LapTelemetryIngestionState(
                status=ingestion.status,
                attempt_count=ingestion.attempt_count,
                sample_count=ingestion.sample_count,
                requested_at=ingestion.requested_at,
                heartbeat_at=ingestion.heartbeat_at,
                next_retry_at=ingestion.next_retry_at,
                completed_at=ingestion.completed_at,
                last_error=(
                    LastError(
                        code=ingestion.last_error_code,
                        message=ingestion.last_error_message,
                    )
                    if ingestion.last_error_code is not None
                    and ingestion.last_error_message is not None
                    else None
                ),
            ),
            page=LapTelemetryPage(
                limit=limit,
                has_more=has_more,
                next_after_sample=(
                    page_rows[-1].sample_index if has_more else None
                ),
            ),
            items=tuple(
                LapTelemetrySampleData(
                    sample_index=sample.sample_index,
                    lap_time_us=sample.lap_time_us,
                    session_time_us=sample.session_time_us,
                    distance_m=sample.distance_m,
                    relative_distance=sample.relative_distance,
                    speed_kph=sample.speed_kph,
                    rpm=sample.rpm,
                    gear=sample.gear,
                    throttle_percent=sample.throttle_percent,
                    brake=sample.brake,
                    drs=sample.drs,
                    x=sample.x,
                    y=sample.y,
                    z=sample.z,
                )
                for sample in page_rows
            ),
        )
