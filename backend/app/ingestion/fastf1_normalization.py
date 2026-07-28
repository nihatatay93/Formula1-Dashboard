from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

import pandas as pd
from pandas import DataFrame

ARCHIVE_SOURCE = "fastf1_archive"
FINALIZED_STATE = "finalized"
RACE_LIKE_SESSION_NAMES = frozenset({"race", "sprint"})


class FastF1NormalizationError(ValueError):
    """Raised when a FastF1 session cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class NormalizedDriver:
    jolpica_driver_id: str
    given_name: str | None
    family_name: str | None
    full_name: str
    country_code: str | None


@dataclass(frozen=True, slots=True)
class NormalizedSessionEntry:
    entry_key: str
    jolpica_driver_id: str | None
    racing_number: str | None
    abbreviation: str | None
    broadcast_name: str | None
    display_name: str
    team_jolpica_id: str | None
    team_name: str | None
    team_color: str | None
    source: str = ARCHIVE_SOURCE
    record_state: str = FINALIZED_STATE


@dataclass(frozen=True, slots=True)
class NormalizedSessionResult:
    entry_key: str
    position: int | None
    classified_position: str | None
    grid_position: int | None
    points: Decimal | None
    status: str | None
    laps_completed: int | None
    q1_time_us: int | None
    q2_time_us: int | None
    q3_time_us: int | None
    elapsed_time_us: int | None
    gap_to_leader_us: int | None
    gap_to_leader_laps: int | None
    source: str = ARCHIVE_SOURCE
    record_state: str = FINALIZED_STATE


@dataclass(frozen=True, slots=True)
class NormalizedLap:
    entry_key: str
    lap_number: int
    stint_number: int | None
    session_time_us: int | None
    lap_time_us: int | None
    lap_start_time_us: int | None
    pit_out_time_us: int | None
    pit_in_time_us: int | None
    sector_1_time_us: int | None
    sector_2_time_us: int | None
    sector_3_time_us: int | None
    sector_1_session_time_us: int | None
    sector_2_session_time_us: int | None
    sector_3_session_time_us: int | None
    speed_i1_kph: float | None
    speed_i2_kph: float | None
    speed_fl_kph: float | None
    speed_st_kph: float | None
    is_personal_best: bool
    compound: str | None
    tyre_life_laps: int | None
    fresh_tyre: bool | None
    track_status: str | None
    position: int | None
    deleted: bool | None
    deleted_reason: str | None
    fastf1_generated: bool
    is_accurate: bool
    source: str = ARCHIVE_SOURCE
    record_state: str = FINALIZED_STATE


@dataclass(frozen=True, slots=True)
class NormalizedSession:
    drivers: tuple[NormalizedDriver, ...]
    entries: tuple[NormalizedSessionEntry, ...]
    results: tuple[NormalizedSessionResult, ...]
    laps: tuple[NormalizedLap, ...]


@dataclass(frozen=True, slots=True)
class _ResultDraft:
    row_number: int
    entry_key: str
    position: int | None
    classified_position: str | None
    grid_position: int | None
    points: Decimal | None
    status: str | None
    laps_completed: int | None
    q1_time_us: int | None
    q2_time_us: int | None
    q3_time_us: int | None
    source_time_us: int | None


def normalize_fastf1_session(
    results: DataFrame,
    laps: DataFrame,
    *,
    session_name: str,
) -> NormalizedSession:
    """Convert one fully loaded FastF1 session into a validated archive snapshot."""

    normalized_session_name = _required_text(session_name, "session_name").casefold()
    race_like = normalized_session_name in RACE_LIKE_SESSION_NAMES

    if results.empty:
        raise FastF1NormalizationError("results must contain at least one session entry")

    drivers: list[NormalizedDriver] = []
    entries: list[NormalizedSessionEntry] = []
    result_drafts: list[_ResultDraft] = []
    entry_keys: set[str] = set()
    driver_ids: set[str] = set()
    racing_numbers: set[str] = set()
    abbreviations: set[str] = set()

    for row_number, row in enumerate(results.to_dict(orient="records"), start=1):
        driver_id = _optional_identifier(row.get("DriverId"))
        racing_number = _optional_racing_number(
            row.get("DriverNumber"),
            f"results row {row_number} DriverNumber",
        )
        entry_key = _entry_key(
            driver_id=driver_id,
            racing_number=racing_number,
            row_number=row_number,
        )

        if entry_key in entry_keys:
            raise FastF1NormalizationError(
                f"results row {row_number} duplicates entry key {entry_key!r}"
            )
        entry_keys.add(entry_key)

        if driver_id is not None:
            if driver_id in driver_ids:
                raise FastF1NormalizationError(
                    f"results row {row_number} duplicates driver ID {driver_id!r}"
                )
            driver_ids.add(driver_id)

        if racing_number is not None:
            if racing_number in racing_numbers:
                raise FastF1NormalizationError(
                    f"results row {row_number} duplicates racing number {racing_number!r}"
                )
            racing_numbers.add(racing_number)

        abbreviation = _optional_text(row.get("Abbreviation"))
        if abbreviation is not None:
            abbreviation = abbreviation.upper()
            if abbreviation in abbreviations:
                raise FastF1NormalizationError(
                    f"results row {row_number} duplicates abbreviation {abbreviation!r}"
                )
            abbreviations.add(abbreviation)

        broadcast_name = _optional_text(row.get("BroadcastName"))
        given_name = _optional_text(row.get("FirstName"))
        family_name = _optional_text(row.get("LastName"))
        full_name = _optional_text(row.get("FullName"))
        display_name = (
            full_name
            or _joined_name(given_name, family_name)
            or broadcast_name
            or abbreviation
        )
        if display_name is None:
            raise FastF1NormalizationError(
                f"results row {row_number} has no usable display name"
            )

        if driver_id is not None:
            drivers.append(
                NormalizedDriver(
                    jolpica_driver_id=driver_id,
                    given_name=given_name,
                    family_name=family_name,
                    full_name=full_name or display_name,
                    country_code=_optional_text(row.get("CountryCode")),
                )
            )

        entries.append(
            NormalizedSessionEntry(
                entry_key=entry_key,
                jolpica_driver_id=driver_id,
                racing_number=racing_number,
                abbreviation=abbreviation,
                broadcast_name=broadcast_name,
                display_name=display_name,
                team_jolpica_id=_optional_identifier(row.get("TeamId")),
                team_name=_optional_text(row.get("TeamName")),
                team_color=_optional_text(row.get("TeamColor")),
            )
        )

        result_drafts.append(
            _ResultDraft(
                row_number=row_number,
                entry_key=entry_key,
                position=_optional_integral(
                    row.get("Position"),
                    f"results row {row_number} Position",
                    minimum=1,
                ),
                classified_position=_optional_classified_position(
                    row.get("ClassifiedPosition"),
                    f"results row {row_number} ClassifiedPosition",
                ),
                grid_position=_optional_integral(
                    row.get("GridPosition"),
                    f"results row {row_number} GridPosition",
                    minimum=0,
                ),
                points=_optional_decimal(
                    row.get("Points"),
                    f"results row {row_number} Points",
                    minimum=Decimal(0),
                    maximum=Decimal("9999.999"),
                ),
                status=_optional_text(row.get("Status")),
                laps_completed=_optional_integral(
                    row.get("Laps"),
                    f"results row {row_number} Laps",
                    minimum=0,
                ),
                q1_time_us=_optional_duration_us(
                    row.get("Q1"),
                    f"results row {row_number} Q1",
                ),
                q2_time_us=_optional_duration_us(
                    row.get("Q2"),
                    f"results row {row_number} Q2",
                ),
                q3_time_us=_optional_duration_us(
                    row.get("Q3"),
                    f"results row {row_number} Q3",
                ),
                source_time_us=_optional_duration_us(
                    row.get("Time"),
                    f"results row {row_number} Time",
                ),
            )
        )

    normalized_results = _normalize_results(result_drafts, race_like=race_like)
    normalized_laps = _normalize_laps(laps, entries)

    return NormalizedSession(
        drivers=tuple(drivers),
        entries=tuple(entries),
        results=normalized_results,
        laps=normalized_laps,
    )


def _normalize_results(
    drafts: list[_ResultDraft],
    *,
    race_like: bool,
) -> tuple[NormalizedSessionResult, ...]:
    winner_laps: int | None = None

    if race_like:
        winners = [draft for draft in drafts if draft.position == 1]
        if len(winners) != 1:
            raise FastF1NormalizationError(
                "race and sprint sessions must contain exactly one first-place result"
            )
        winner_laps = winners[0].laps_completed

    normalized_results: list[NormalizedSessionResult] = []
    for draft in drafts:
        elapsed_time_us: int | None = None
        gap_to_leader_us: int | None = None
        gap_to_leader_laps: int | None = None

        if race_like:
            if draft.position == 1:
                elapsed_time_us = draft.source_time_us
                gap_to_leader_us = 0
                gap_to_leader_laps = 0
            else:
                gap_to_leader_us = draft.source_time_us
                if winner_laps is not None and draft.laps_completed is not None:
                    gap_to_leader_laps = max(winner_laps - draft.laps_completed, 0)

        normalized_results.append(
            NormalizedSessionResult(
                entry_key=draft.entry_key,
                position=draft.position,
                classified_position=draft.classified_position,
                grid_position=draft.grid_position,
                points=draft.points,
                status=draft.status,
                laps_completed=draft.laps_completed,
                q1_time_us=draft.q1_time_us,
                q2_time_us=draft.q2_time_us,
                q3_time_us=draft.q3_time_us,
                elapsed_time_us=elapsed_time_us,
                gap_to_leader_us=gap_to_leader_us,
                gap_to_leader_laps=gap_to_leader_laps,
            )
        )

    return tuple(normalized_results)


def _normalize_laps(
    laps: DataFrame,
    entries: list[NormalizedSessionEntry],
) -> tuple[NormalizedLap, ...]:
    entries_by_number = {
        entry.racing_number: entry
        for entry in entries
        if entry.racing_number is not None
    }
    entries_by_abbreviation = {
        entry.abbreviation: entry
        for entry in entries
        if entry.abbreviation is not None
    }
    normalized_laps: list[NormalizedLap] = []
    lap_keys: set[tuple[str, int]] = set()

    for row_number, row in enumerate(laps.to_dict(orient="records"), start=1):
        entry = _lap_entry(
            row,
            row_number=row_number,
            entries_by_number=entries_by_number,
            entries_by_abbreviation=entries_by_abbreviation,
        )
        lap_number = _required_integral(
            row.get("LapNumber"),
            f"laps row {row_number} LapNumber",
            minimum=1,
        )
        lap_key = (entry.entry_key, lap_number)
        if lap_key in lap_keys:
            raise FastF1NormalizationError(
                f"laps row {row_number} duplicates lap {lap_number} for {entry.entry_key!r}"
            )
        lap_keys.add(lap_key)

        normalized_laps.append(
            NormalizedLap(
                entry_key=entry.entry_key,
                lap_number=lap_number,
                stint_number=_optional_integral(
                    row.get("Stint"),
                    f"laps row {row_number} Stint",
                    minimum=1,
                ),
                session_time_us=_optional_duration_us(
                    row.get("Time"),
                    f"laps row {row_number} Time",
                ),
                lap_time_us=_optional_duration_us(
                    row.get("LapTime"),
                    f"laps row {row_number} LapTime",
                ),
                lap_start_time_us=_optional_duration_us(
                    row.get("LapStartTime"),
                    f"laps row {row_number} LapStartTime",
                ),
                pit_out_time_us=_optional_duration_us(
                    row.get("PitOutTime"),
                    f"laps row {row_number} PitOutTime",
                ),
                pit_in_time_us=_optional_duration_us(
                    row.get("PitInTime"),
                    f"laps row {row_number} PitInTime",
                ),
                sector_1_time_us=_optional_duration_us(
                    row.get("Sector1Time"),
                    f"laps row {row_number} Sector1Time",
                ),
                sector_2_time_us=_optional_duration_us(
                    row.get("Sector2Time"),
                    f"laps row {row_number} Sector2Time",
                ),
                sector_3_time_us=_optional_duration_us(
                    row.get("Sector3Time"),
                    f"laps row {row_number} Sector3Time",
                ),
                sector_1_session_time_us=_optional_duration_us(
                    row.get("Sector1SessionTime"),
                    f"laps row {row_number} Sector1SessionTime",
                ),
                sector_2_session_time_us=_optional_duration_us(
                    row.get("Sector2SessionTime"),
                    f"laps row {row_number} Sector2SessionTime",
                ),
                sector_3_session_time_us=_optional_duration_us(
                    row.get("Sector3SessionTime"),
                    f"laps row {row_number} Sector3SessionTime",
                ),
                speed_i1_kph=_optional_float(
                    row.get("SpeedI1"),
                    f"laps row {row_number} SpeedI1",
                    minimum=0,
                ),
                speed_i2_kph=_optional_float(
                    row.get("SpeedI2"),
                    f"laps row {row_number} SpeedI2",
                    minimum=0,
                ),
                speed_fl_kph=_optional_float(
                    row.get("SpeedFL"),
                    f"laps row {row_number} SpeedFL",
                    minimum=0,
                ),
                speed_st_kph=_optional_float(
                    row.get("SpeedST"),
                    f"laps row {row_number} SpeedST",
                    minimum=0,
                ),
                is_personal_best=_required_bool(
                    row.get("IsPersonalBest"),
                    f"laps row {row_number} IsPersonalBest",
                ),
                compound=_optional_text(row.get("Compound")),
                tyre_life_laps=_optional_integral(
                    row.get("TyreLife"),
                    f"laps row {row_number} TyreLife",
                    minimum=0,
                ),
                fresh_tyre=_optional_bool(
                    row.get("FreshTyre"),
                    f"laps row {row_number} FreshTyre",
                ),
                track_status=_optional_text(row.get("TrackStatus")),
                position=_optional_integral(
                    row.get("Position"),
                    f"laps row {row_number} Position",
                    minimum=1,
                ),
                deleted=_optional_bool(
                    row.get("Deleted"),
                    f"laps row {row_number} Deleted",
                ),
                deleted_reason=_optional_text(row.get("DeletedReason")),
                fastf1_generated=_required_bool(
                    row.get("FastF1Generated"),
                    f"laps row {row_number} FastF1Generated",
                ),
                is_accurate=_required_bool(
                    row.get("IsAccurate"),
                    f"laps row {row_number} IsAccurate",
                ),
            )
        )

    return tuple(normalized_laps)


def _lap_entry(
    row: dict[str, Any],
    *,
    row_number: int,
    entries_by_number: dict[str, NormalizedSessionEntry],
    entries_by_abbreviation: dict[str, NormalizedSessionEntry],
) -> NormalizedSessionEntry:
    racing_number = _optional_racing_number(
        row.get("DriverNumber"),
        f"laps row {row_number} DriverNumber",
    )
    abbreviation = _optional_text(row.get("Driver"))
    if abbreviation is not None:
        abbreviation = abbreviation.upper()

    if racing_number is not None:
        entry = entries_by_number.get(racing_number)
        if entry is None:
            raise FastF1NormalizationError(
                f"laps row {row_number} references unknown racing number {racing_number!r}"
            )
        if (
            abbreviation is not None
            and entry.abbreviation is not None
            and abbreviation != entry.abbreviation
        ):
            raise FastF1NormalizationError(
                f"laps row {row_number} driver abbreviation does not match racing number"
            )
        return entry

    if abbreviation is not None:
        entry = entries_by_abbreviation.get(abbreviation)
        if entry is not None:
            return entry

    raise FastF1NormalizationError(
        f"laps row {row_number} cannot be matched to a session entry"
    )


def _entry_key(
    *,
    driver_id: str | None,
    racing_number: str | None,
    row_number: int,
) -> str:
    if driver_id is not None:
        return f"driver:jolpica:{driver_id}"
    if racing_number is not None:
        return f"car-number:{racing_number}"
    raise FastF1NormalizationError(
        f"results row {row_number} has neither a driver ID nor a racing number"
    )


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    missing = pd.isna(value)
    if isinstance(missing, bool):
        return missing
    if missing.__class__.__name__ == "bool_":
        return bool(missing)
    raise FastF1NormalizationError("normalization received a non-scalar value")


def _optional_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(value: Any, field: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise FastF1NormalizationError(f"{field} is required")
    return normalized


def _optional_identifier(value: Any) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    return normalized.casefold()


def _optional_racing_number(value: Any, field: str) -> str | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    return str(_required_integral(value, field, minimum=1))


def _optional_classified_position(value: Any, field: str) -> str | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None

    if isinstance(value, str):
        candidate = value.strip()
        try:
            Decimal(candidate)
        except InvalidOperation:
            normalized = candidate.upper()
            if any(character.isspace() for character in normalized):
                raise FastF1NormalizationError(f"{field} must not contain whitespace")
            return normalized

    return str(_required_integral(value, field, minimum=1))


def _optional_integral(
    value: Any,
    field: str,
    *,
    minimum: int,
) -> int | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    return _required_integral(value, field, minimum=minimum)


def _required_integral(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool):
        raise FastF1NormalizationError(f"{field} must be an integer")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FastF1NormalizationError(f"{field} must be an integer") from None
    if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
        raise FastF1NormalizationError(f"{field} must be an integer")
    normalized = int(decimal_value)
    if normalized < minimum:
        raise FastF1NormalizationError(f"{field} must be at least {minimum}")
    return normalized


def _optional_decimal(
    value: Any,
    field: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FastF1NormalizationError(f"{field} must be a decimal value") from None
    if not normalized.is_finite() or normalized < minimum or normalized > maximum:
        raise FastF1NormalizationError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return normalized


def _optional_duration_us(value: Any, field: str) -> int | None:
    if _is_missing(value):
        return None

    if isinstance(value, pd.Timedelta):
        nanoseconds = int(value.value)
        microseconds, remainder = divmod(nanoseconds, 1_000)
        if remainder:
            raise FastF1NormalizationError(
                f"{field} cannot be represented as whole microseconds"
            )
    elif isinstance(value, timedelta):
        microseconds = (
            (value.days * 86_400 + value.seconds) * 1_000_000
            + value.microseconds
        )
    else:
        raise FastF1NormalizationError(f"{field} must be a timedelta")

    if microseconds < 0:
        raise FastF1NormalizationError(f"{field} must not be negative")
    return microseconds


def _optional_float(
    value: Any,
    field: str,
    *,
    minimum: float,
) -> float | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise FastF1NormalizationError(f"{field} must be numeric")
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise FastF1NormalizationError(f"{field} must be numeric") from None
    if not isfinite(normalized) or normalized < minimum:
        raise FastF1NormalizationError(f"{field} must be at least {minimum}")
    return normalized


def _optional_bool(value: Any, field: str) -> bool | None:
    if _is_missing(value):
        return None
    return _required_bool(value, field)


def _required_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value.__class__.__module__.startswith("numpy") and value.__class__.__name__ == "bool_":
        return bool(value)
    raise FastF1NormalizationError(f"{field} must be a boolean")


def _joined_name(given_name: str | None, family_name: str | None) -> str | None:
    joined = " ".join(part for part in (given_name, family_name) if part)
    return joined or None
