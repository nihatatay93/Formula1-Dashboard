from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from numbers import Integral
from pathlib import Path
from typing import Protocol

import fastf1
import pandas as pd

from app.ingestion.fastf1_loader import (
    MINIMUM_ARCHIVE_YEAR,
    FastF1LoaderConfigurationError,
    FastF1RequestBudgetProtocol,
    FastF1SessionLoader,
    serialized_fastf1_access,
)
from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)


class FastF1ScheduleError(RuntimeError):
    """Base error for season-schedule discovery."""


class FastF1ScheduleLoadError(FastF1ScheduleError):
    """Raised when FastF1 cannot return a season schedule."""


class FastF1ScheduleNormalizationError(FastF1ScheduleError):
    """Raised when an upstream schedule snapshot is incomplete or ambiguous."""


class FastF1ScheduleRoundConflictError(FastF1ScheduleNormalizationError):
    """Raised when the private schedule assigns one round more than once."""


@dataclass(frozen=True, slots=True)
class NormalizedScheduledSession:
    session_key: str
    session_name: str
    scheduled_start_at: datetime
    scheduled_end_at: datetime


@dataclass(frozen=True, slots=True)
class NormalizedScheduledEvent:
    round_number: int
    official_name: str | None
    event_name: str
    country: str | None
    location: str | None
    event_format: str
    starts_at: datetime
    ends_at: datetime
    sessions: tuple[NormalizedScheduledSession, ...]


@dataclass(frozen=True, slots=True)
class NormalizedSeasonSchedule:
    season_year: int
    events: tuple[NormalizedScheduledEvent, ...]


class FastF1ScheduleLoaderProtocol(Protocol):
    def load(self, season_year: int) -> NormalizedSeasonSchedule: ...


_CANONICAL_SESSION_KEYS = {
    "practice 1": "practice_1",
    "practice 2": "practice_2",
    "practice 3": "practice_3",
    "qualifying": "qualifying",
    "sprint": "sprint",
    "sprint qualifying": "sprint_qualifying",
    "sprint shootout": "sprint_shootout",
    "race": "race",
}


class FastF1ScheduleLoader:
    """Load one championship schedule through FastF1's serialized cache."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        request_budget: FastF1RequestBudgetProtocol | None = None,
    ) -> None:
        self._cache_client = FastF1SessionLoader(
            cache_path,
            request_budget=request_budget,
        )
        self.cache_path = self._cache_client.cache_path

    def load(self, season_year: int) -> NormalizedSeasonSchedule:
        _validate_season_year(season_year)

        # FastF1 3.8.3's public EventSchedule drops session EndDate values.
        # Its pinned, cache-wrapped season index retains both boundaries but
        # can omit supported events. The curated schedule defines membership
        # and round numbers; exact timing metadata fills any missing event.
        with serialized_fastf1_access(self._cache_client):
            try:
                meetings = fastf1._api.season_schedule(
                    f"/static/{season_year}/"
                )
                public_schedule = fastf1.get_event_schedule(
                    season_year,
                    include_testing=False,
                    backend="fastf1",
                )
            except FastF1RequestBudgetExhaustedError:
                raise
            except Exception as error:
                raise FastF1ScheduleLoadError(
                    f"FastF1 failed to load the {season_year} schedule"
                ) from error

            round_numbers_by_event_name = (
                curated_round_numbers_by_event_name(public_schedule)
            )
            missing_events = _missing_curated_events(
                public_schedule,
                meetings,
            )
            reconciled_meetings = list(meetings)
            for missing_event in missing_events:
                try:
                    reconciled_meetings.append(
                        _load_missing_curated_event(
                            season_year,
                            missing_event,
                        )
                    )
                except FastF1RequestBudgetExhaustedError:
                    raise
                except FastF1ScheduleNormalizationError:
                    raise
                except Exception as error:
                    event_name = _required_text(
                        missing_event.get("EventName"),
                        "curated event EventName",
                    )
                    raise FastF1ScheduleLoadError(
                        f"FastF1 failed to load exact session metadata "
                        f"for {season_year} {event_name}"
                    ) from error

            return normalize_fastf1_schedule(
                season_year=season_year,
                meetings=reconciled_meetings,
                round_numbers_by_event_name=(
                    round_numbers_by_event_name
                )
            )


def create_fastf1_schedule_loader(
    cache_path: str | Path | None = None,
    *,
    request_budget: FastF1RequestBudgetProtocol | None = None,
) -> FastF1ScheduleLoader:
    configured_path = cache_path
    if configured_path is None:
        configured_path = os.environ.get("FASTF1_CACHE_PATH")
    if configured_path is None or not str(configured_path).strip():
        raise FastF1LoaderConfigurationError(
            "FASTF1_CACHE_PATH is required"
        )
    return FastF1ScheduleLoader(
        configured_path,
        request_budget=request_budget,
    )


def normalize_fastf1_schedule(
    *,
    season_year: int,
    meetings: object,
    round_numbers_by_event_name: Mapping[str, int] | None = None,
) -> NormalizedSeasonSchedule:
    """Normalize FastF1's season-index meetings without network or DB writes."""

    _validate_season_year(season_year)
    if not isinstance(meetings, Sequence) or isinstance(
        meetings,
        str | bytes,
    ):
        raise FastF1ScheduleNormalizationError(
            "FastF1 schedule meetings must be a sequence"
        )

    events: list[NormalizedScheduledEvent] = []
    round_numbers: set[int] = set()
    for raw_event in meetings:
        if not isinstance(raw_event, Mapping):
            raise FastF1ScheduleNormalizationError(
                "FastF1 schedule event must be a mapping"
            )

        raw_round_number = _positive_integer(
            raw_event.get("Number"),
            "event Number",
        )
        event_name = _required_text(
            raw_event.get("Name"),
            "event Name",
        )
        if raw_round_number == 0 or "test" in event_name.casefold():
            continue
        if round_numbers_by_event_name is None:
            round_number = raw_round_number
        else:
            event_key = _event_name_key(event_name)
            try:
                round_number = round_numbers_by_event_name[event_key]
            except KeyError:
                raise FastF1ScheduleNormalizationError(
                    f"event {event_name!r} is absent from the curated schedule"
                ) from None
            round_number = _positive_integer(
                round_number,
                f"event {event_name!r} curated RoundNumber",
            )
            if round_number == 0:
                raise FastF1ScheduleNormalizationError(
                    f"event {event_name!r} has no championship round"
                )
        if round_number in round_numbers:
            error_type = (
                FastF1ScheduleRoundConflictError
                if round_numbers_by_event_name is None
                else FastF1ScheduleNormalizationError
            )
            raise error_type(f"duplicate championship round {round_number}")

        raw_sessions = raw_event.get("Sessions")
        if not isinstance(raw_sessions, Sequence) or isinstance(
            raw_sessions,
            str | bytes,
        ):
            raise FastF1ScheduleNormalizationError(
                f"round {round_number} Sessions must be a sequence"
            )

        valid_sessions = tuple(
            raw_session
            for raw_session in raw_sessions
            if (
                isinstance(raw_session, Mapping)
                and raw_session.get("Key") != -1
                and _optional_text(raw_session.get("Name")) is not None
            )
        )[:5]
        if not valid_sessions:
            raise FastF1ScheduleNormalizationError(
                f"round {round_number} has no usable sessions"
            )

        normalized_sessions: list[NormalizedScheduledSession] = []
        session_keys: set[str] = set()
        for raw_session in valid_sessions:
            session_name = _required_text(
                raw_session.get("Name"),
                f"round {round_number} session Name",
            )
            if (
                season_year in (2021, 2022)
                and session_name == "Sprint Qualifying"
            ):
                session_name = "Sprint"

            session_key = _session_key(session_name)
            if session_key in session_keys:
                raise FastF1ScheduleNormalizationError(
                    f"round {round_number} has duplicate session key "
                    f"{session_key!r}"
                )

            offset = _utc_offset(
                raw_session.get("GmtOffset"),
                round_number=round_number,
                session_name=session_name,
            )
            scheduled_start_at = _utc_timestamp(
                raw_session.get("StartDate"),
                offset=offset,
                field="StartDate",
                round_number=round_number,
                session_name=session_name,
            )
            scheduled_end_at = _utc_timestamp(
                raw_session.get("EndDate"),
                offset=offset,
                field="EndDate",
                round_number=round_number,
                session_name=session_name,
            )
            if scheduled_end_at <= scheduled_start_at:
                raise FastF1ScheduleNormalizationError(
                    f"round {round_number} session {session_name!r} "
                    "must end after it starts"
                )

            session_keys.add(session_key)
            normalized_sessions.append(
                NormalizedScheduledSession(
                    session_key=session_key,
                    session_name=session_name,
                    scheduled_start_at=scheduled_start_at,
                    scheduled_end_at=scheduled_end_at,
                )
            )

        session_names = {
            session.session_name
            for session in normalized_sessions
        }
        events.append(
            NormalizedScheduledEvent(
                round_number=round_number,
                official_name=_optional_text(
                    raw_event.get("OfficialName")
                ),
                event_name=event_name,
                country=_country_name(raw_event.get("Country")),
                location=_optional_text(raw_event.get("Location")),
                event_format=_event_format(session_names),
                starts_at=min(
                    session.scheduled_start_at
                    for session in normalized_sessions
                ),
                ends_at=max(
                    session.scheduled_end_at
                    for session in normalized_sessions
                ),
                sessions=tuple(normalized_sessions),
            )
        )
        round_numbers.add(round_number)

    if not events:
        raise FastF1ScheduleNormalizationError(
            f"FastF1 returned no championship events for {season_year}"
        )

    return NormalizedSeasonSchedule(
        season_year=season_year,
        events=tuple(sorted(events, key=lambda event: event.round_number)),
    )


def curated_round_numbers_by_event_name(
    schedule: object,
) -> dict[str, int]:
    """Validate the public FastF1 schedule as an event-name round authority."""

    if not isinstance(schedule, pd.DataFrame):
        raise FastF1ScheduleNormalizationError(
            "FastF1 curated schedule must be a DataFrame"
        )
    if not {"EventName", "RoundNumber"} <= set(schedule.columns):
        raise FastF1ScheduleNormalizationError(
            "FastF1 curated schedule is missing required columns"
        )

    round_numbers: dict[str, int] = {}
    used_rounds: set[int] = set()
    for row_number, row in schedule.iterrows():
        event_name = _required_text(
            row.get("EventName"),
            f"curated schedule row {row_number} EventName",
        )
        round_number = _positive_integer(
            row.get("RoundNumber"),
            f"curated schedule row {row_number} RoundNumber",
        )
        if round_number == 0:
            continue

        event_key = _event_name_key(event_name)
        if event_key in round_numbers:
            raise FastF1ScheduleNormalizationError(
                f"duplicate curated event name {event_name!r}"
            )
        if round_number in used_rounds:
            raise FastF1ScheduleNormalizationError(
                f"duplicate curated championship round {round_number}"
            )
        round_numbers[event_key] = round_number
        used_rounds.add(round_number)

    if not round_numbers:
        raise FastF1ScheduleNormalizationError(
            "FastF1 curated schedule has no championship events"
        )
    return round_numbers


def _missing_curated_events(
    schedule: object,
    meetings: object,
) -> tuple[pd.Series, ...]:
    if not isinstance(schedule, pd.DataFrame):
        raise FastF1ScheduleNormalizationError(
            "FastF1 curated schedule must be a DataFrame"
        )
    if not isinstance(meetings, Sequence) or isinstance(
        meetings,
        str | bytes,
    ):
        raise FastF1ScheduleNormalizationError(
            "FastF1 schedule meetings must be a sequence"
        )

    private_event_keys: set[str] = set()
    for raw_event in meetings:
        if not isinstance(raw_event, Mapping):
            raise FastF1ScheduleNormalizationError(
                "FastF1 schedule event must be a mapping"
            )
        event_name = _required_text(
            raw_event.get("Name"),
            "event Name",
        )
        if "test" not in event_name.casefold():
            private_event_keys.add(_event_name_key(event_name))

    missing_events = []
    for row_number, row in schedule.iterrows():
        round_number = _positive_integer(
            row.get("RoundNumber"),
            f"curated schedule row {row_number} RoundNumber",
        )
        if round_number == 0:
            continue
        event_name = _required_text(
            row.get("EventName"),
            f"curated schedule row {row_number} EventName",
        )
        if _event_name_key(event_name) not in private_event_keys:
            missing_events.append(row)
    return tuple(missing_events)


def _load_missing_curated_event(
    season_year: int,
    event: pd.Series,
) -> dict[str, object]:
    round_number = _positive_integer(
        event.get("RoundNumber"),
        "curated event RoundNumber",
    )
    event_name = _required_text(
        event.get("EventName"),
        "curated event EventName",
    )
    session_names = tuple(
        session_name
        for index in range(1, 6)
        if (
            session_name := _optional_text(
                event.get(f"Session{index}")
            )
        )
        is not None
    )
    if not session_names:
        raise FastF1ScheduleNormalizationError(
            f"curated event {event_name!r} has no usable sessions"
        )

    sessions = []
    for session_name in session_names:
        session = fastf1.get_session(
            season_year,
            round_number,
            session_name,
            backend="fastf1",
        )
        api_path = _required_text(
            getattr(session, "api_path", None),
            f"round {round_number} session {session_name!r} API path",
        )
        session_info = fastf1._api.session_info(api_path)
        if not isinstance(session_info, Mapping):
            raise FastF1ScheduleNormalizationError(
                f"round {round_number} session {session_name!r} "
                "metadata must be a mapping"
            )
        sessions.append(
            {
                "Key": session_info.get("Key"),
                "Name": session_name,
                "StartDate": session_info.get("StartDate"),
                "EndDate": session_info.get("EndDate"),
                "GmtOffset": session_info.get("GmtOffset"),
                "Path": api_path,
            }
        )

    return {
        "Number": round_number,
        "Name": event_name,
        "OfficialName": _optional_text(
            event.get("OfficialEventName")
        ),
        "Country": _optional_text(event.get("Country")),
        "Location": _optional_text(event.get("Location")),
        "Sessions": sessions,
    }


def _validate_season_year(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MINIMUM_ARCHIVE_YEAR
        or value > 32767
    ):
        raise FastF1LoaderConfigurationError(
            f"season_year must be between {MINIMUM_ARCHIVE_YEAR} and 32767"
        )


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise FastF1ScheduleNormalizationError(
            f"{field} must be a non-negative integer"
        )
    if value > 32767:
        raise FastF1ScheduleNormalizationError(
            f"{field} exceeds the database range"
        )
    return int(value)


def _required_text(value: object, field: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise FastF1ScheduleNormalizationError(
            f"{field} must be non-empty text"
        )
    return normalized


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _country_name(value: object) -> str | None:
    if isinstance(value, Mapping):
        return _optional_text(value.get("Name"))
    return _optional_text(value)


def _event_name_key(event_name: str) -> str:
    return " ".join(event_name.casefold().split())


def _session_key(session_name: str) -> str:
    known = _CANONICAL_SESSION_KEYS.get(session_name.casefold())
    if known is not None:
        return known

    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        session_name.casefold(),
    ).strip("_")
    if not normalized:
        raise FastF1ScheduleNormalizationError(
            f"session name {session_name!r} has no usable canonical key"
        )
    return normalized


def _event_format(session_names: set[str]) -> str:
    if "Sprint Shootout" in session_names:
        return "sprint_shootout"
    if "Sprint Qualifying" in session_names:
        return "sprint_qualifying"
    if "Sprint" in session_names:
        return "sprint"
    return "conventional"


def _utc_offset(
    value: object,
    *,
    round_number: int,
    session_name: str,
) -> timezone:
    try:
        if isinstance(value, timedelta):
            offset = value
        elif isinstance(value, str):
            match = re.fullmatch(
                r"(?P<sign>[+-]?)(?P<hours>\d{1,2}):"
                r"(?P<minutes>\d{2})(?::(?P<seconds>\d{2}))?",
                value.strip(),
            )
            if match is None:
                raise ValueError("invalid offset")
            sign = -1 if match.group("sign") == "-" else 1
            offset = sign * timedelta(
                hours=int(match.group("hours")),
                minutes=int(match.group("minutes")),
                seconds=int(match.group("seconds") or 0),
            )
        else:
            raise ValueError("invalid offset")
        return timezone(offset)
    except Exception as error:
        raise FastF1ScheduleNormalizationError(
            f"round {round_number} session {session_name!r} "
            "has an invalid GmtOffset"
        ) from error


def _utc_timestamp(
    value: object,
    *,
    offset: timezone,
    field: str,
    round_number: int,
    session_name: str,
) -> datetime:
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError("missing timestamp")
        candidate = timestamp.to_pydatetime()
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            candidate = candidate.replace(tzinfo=offset)
        return candidate.astimezone(UTC)
    except Exception as error:
        raise FastF1ScheduleNormalizationError(
            f"round {round_number} session {session_name!r} "
            f"has an invalid {field}"
        ) from error
