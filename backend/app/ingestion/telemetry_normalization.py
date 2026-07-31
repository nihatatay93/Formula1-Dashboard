from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd
from pandas import DataFrame


class TelemetryNormalizationError(ValueError):
    """Raised when a FastF1 telemetry frame cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class NormalizedTelemetrySample:
    sample_index: int
    lap_time_us: int
    session_time_us: int | None
    distance_m: float | None
    relative_distance: float | None
    speed_kph: float | None
    rpm: int | None
    gear: int | None
    throttle_percent: float | None
    brake: bool | None
    drs: int | None
    x: float | None
    y: float | None
    z: float | None


def normalize_fastf1_telemetry(
    frame: DataFrame,
) -> tuple[NormalizedTelemetrySample, ...]:
    """Normalize one exact lap into deterministic, validated sample rows."""

    if not isinstance(frame, DataFrame) or frame.empty:
        raise TelemetryNormalizationError("telemetry frame must not be empty")
    if "Time" not in frame.columns:
        raise TelemetryNormalizationError("telemetry frame requires Time")

    rows: list[NormalizedTelemetrySample] = []
    previous_time = -1
    for sample_index, (_, source) in enumerate(frame.reset_index(drop=True).iterrows()):
        lap_time_us = _duration_us(source.get("Time"), required=True)
        assert lap_time_us is not None
        if lap_time_us <= previous_time:
            raise TelemetryNormalizationError(
                "telemetry Time must be strictly increasing"
            )
        previous_time = lap_time_us

        rows.append(
            NormalizedTelemetrySample(
                sample_index=sample_index,
                lap_time_us=lap_time_us,
                session_time_us=_duration_us(source.get("SessionTime")),
                distance_m=_float(source.get("Distance"), minimum=0),
                relative_distance=_float(
                    source.get("RelativeDistance"),
                    minimum=0,
                    maximum=1.01,
                ),
                speed_kph=_float(source.get("Speed"), minimum=0),
                # RPM is a continuous measurement, and FastF1 returns it already
                # interpolated onto the merged car/position time base, so it is
                # legitimately fractional and is rounded rather than rejected.
                # Gear and DRS below stay strict: those are discrete states that
                # the upstream carries forward rather than interpolating, so a
                # fractional value there really would mean a corrupt snapshot.
                rpm=_integer(source.get("RPM"), minimum=0, rounded=True),
                gear=_integer(source.get("nGear"), minimum=0, maximum=20),
                throttle_percent=_float(
                    source.get("Throttle"),
                    minimum=0,
                    maximum=100,
                ),
                brake=_boolean(source.get("Brake")),
                drs=_integer(source.get("DRS"), minimum=0, maximum=20),
                x=_float(source.get("X")),
                y=_float(source.get("Y")),
                z=_float(source.get("Z")),
            )
        )
    return tuple(rows)


def _missing(value: object) -> bool:
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _duration_us(value: object, *, required: bool = False) -> int | None:
    if value is None or _missing(value):
        if required:
            raise TelemetryNormalizationError("Time cannot be null")
        return None
    if isinstance(value, pd.Timedelta):
        microseconds = value.value // 1_000
    elif isinstance(value, timedelta):
        microseconds = round(value.total_seconds() * 1_000_000)
    else:
        raise TelemetryNormalizationError("telemetry time must be a duration")
    if microseconds < 0:
        raise TelemetryNormalizationError("telemetry time cannot be negative")
    return int(microseconds)


def _float(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None or _missing(value):
        return None
    if isinstance(value, bool):
        raise TelemetryNormalizationError("numeric telemetry cannot be boolean")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise TelemetryNormalizationError(
            "telemetry channel must be numeric"
        ) from error
    if not math.isfinite(normalized):
        raise TelemetryNormalizationError(
            "telemetry channel must be finite"
        )
    if minimum is not None and normalized < minimum:
        raise TelemetryNormalizationError("telemetry channel is below range")
    if maximum is not None and normalized > maximum:
        raise TelemetryNormalizationError("telemetry channel is above range")
    return normalized


def _integer(
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    rounded: bool = False,
) -> int | None:
    """Read an integer channel.

    ``rounded`` marks a continuous measurement that the upstream may deliver
    interpolated; without it a fractional value is treated as a corrupt
    snapshot, which is the right answer only for genuinely discrete channels.
    """
    normalized = _float(value, minimum=minimum, maximum=maximum)
    if normalized is None:
        return None
    if rounded:
        return round(normalized)
    integer = int(normalized)
    if normalized != integer:
        raise TelemetryNormalizationError(
            "integer telemetry channel must be integral"
        )
    return integer


def _boolean(value: Any) -> bool | None:
    if value is None or _missing(value):
        return None
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise TelemetryNormalizationError("brake telemetry must be boolean")
