from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, RaceSession
from app.ingestion.archive_persistence import (
    ArchivePersistenceSummary,
    ArchiveSessionIdentity,
    ArchiveSessionNotFoundError,
    replace_archive_session,
)
from app.ingestion.fastf1_loader import (
    FastF1SessionRequest,
    LoadedFastF1Session,
)
from app.ingestion.fastf1_normalization import normalize_fastf1_session


class FastF1SessionLoaderProtocol(Protocol):
    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session: ...


SessionFactory = Callable[[], Session]


class ArchiveIngestionError(RuntimeError):
    """Base error for one-session archive ingestion orchestration failures."""


class ArchiveSessionIdentityError(ArchiveIngestionError):
    """Raised when loaded FastF1 data does not match the database session."""


@dataclass(frozen=True, slots=True)
class ArchiveIngestionSummary:
    request: FastF1SessionRequest
    loaded_session_name: str
    persistence: ArchivePersistenceSummary


def ingest_fastf1_archive_session(
    *,
    session_id: int,
    session_factory: SessionFactory,
    loader: FastF1SessionLoaderProtocol,
    completed_at: datetime | None = None,
    source_updated_at: datetime | None = None,
) -> ArchiveIngestionSummary:
    """Load, normalize, and atomically persist one database session."""

    target = _load_target(session_factory, session_id)
    request = FastF1SessionRequest(
        season_year=target.season_year,
        round_number=target.round_number,
        session_identifier=target.session_name,
    )

    loaded = loader.load(request)
    _validate_loaded_identity(target, request, loaded)
    snapshot = normalize_fastf1_session(
        loaded.results,
        loaded.laps,
        session_name=loaded.session_name,
    )

    with session_factory() as database:
        persistence = replace_archive_session(
            database,
            session_id=target.session_id,
            snapshot=snapshot,
            completed_at=completed_at,
            source_updated_at=source_updated_at,
            expected_identity=target,
        )

    return ArchiveIngestionSummary(
        request=request,
        loaded_session_name=loaded.session_name,
        persistence=persistence,
    )


def _load_target(
    session_factory: SessionFactory,
    session_id: int,
) -> ArchiveSessionIdentity:
    with session_factory() as database:
        row = database.execute(
            select(
                RaceSession.id,
                Event.season_year,
                Event.round_number,
                RaceSession.session_name,
            )
            .join(Event, Event.id == RaceSession.event_id)
            .where(RaceSession.id == session_id)
        ).one_or_none()

    if row is None:
        raise ArchiveSessionNotFoundError(
            f"database session {session_id} does not exist"
        )

    return ArchiveSessionIdentity(
        session_id=row.id,
        season_year=row.season_year,
        round_number=row.round_number,
        session_name=row.session_name,
    )


def _validate_loaded_identity(
    target: ArchiveSessionIdentity,
    request: FastF1SessionRequest,
    loaded: LoadedFastF1Session,
) -> None:
    if loaded.request != request:
        raise ArchiveSessionIdentityError(
            "FastF1 loader returned data for a different request"
        )
    if _canonical_session_name(loaded.session_name) != _canonical_session_name(
        target.session_name
    ):
        raise ArchiveSessionIdentityError(
            f"FastF1 loaded session {loaded.session_name!r}, "
            f"but database session {target.session_id} expects "
            f"{target.session_name!r}"
        )


def _canonical_session_name(value: str) -> str:
    return " ".join(value.split()).casefold()
