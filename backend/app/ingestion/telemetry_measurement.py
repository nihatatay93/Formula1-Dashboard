from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from statistics import median
from typing import Any

import pandas as pd
from pandas import DataFrame

ESTIMATED_POSTGRES_BYTES_PER_SAMPLE = 160
MEASURED_CHANNELS = (
    "Speed",
    "RPM",
    "nGear",
    "Throttle",
    "Brake",
    "DRS",
    "Distance",
    "RelativeDistance",
    "X",
    "Y",
    "Z",
)


class TelemetryMeasurementError(ValueError):
    """Raised when a telemetry frame cannot be measured truthfully."""


@dataclass(frozen=True, slots=True)
class TelemetryMeasurementIdentity:
    season_year: int
    round_number: int
    session_identifier: str
    driver_identifier: str
    lap_number: int

    def __post_init__(self) -> None:
        if self.season_year < 2018:
            raise TelemetryMeasurementError("season_year must be at least 2018")
        if self.round_number < 1:
            raise TelemetryMeasurementError("round_number must be positive")
        if self.lap_number < 1:
            raise TelemetryMeasurementError("lap_number must be positive")
        if not self.session_identifier.strip():
            raise TelemetryMeasurementError(
                "session_identifier must not be empty"
            )
        if not self.driver_identifier.strip():
            raise TelemetryMeasurementError(
                "driver_identifier must not be empty"
            )


@dataclass(frozen=True, slots=True)
class TelemetryChannelMeasurement:
    present: bool
    non_null_samples: int


@dataclass(frozen=True, slots=True)
class TelemetryFrameMeasurement:
    identity: TelemetryMeasurementIdentity
    sample_count: int
    duration_us: int
    median_interval_us: int
    p95_interval_us: int
    samples_per_second: float
    memory_bytes: int
    estimated_postgres_bytes: int
    channels: dict[str, TelemetryChannelMeasurement]

    def as_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TelemetryMeasurementSummary:
    frame_count: int
    total_samples: int
    average_samples_per_lap: int
    median_samples_per_lap: int
    total_memory_bytes: int
    estimated_postgres_bytes: int

    def as_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_telemetry_frame(
    frame: DataFrame,
    *,
    identity: TelemetryMeasurementIdentity,
) -> TelemetryFrameMeasurement:
    if frame.empty:
        raise TelemetryMeasurementError("telemetry frame must not be empty")
    if "Time" not in frame.columns:
        raise TelemetryMeasurementError(
            "telemetry frame must contain the lap-relative Time channel"
        )

    time_values = pd.to_timedelta(frame["Time"], errors="coerce")
    if time_values.isna().any():
        raise TelemetryMeasurementError(
            "telemetry Time values must all be valid durations"
        )
    time_us = [int(value.value // 1_000) for value in time_values]
    if time_us[0] < 0:
        raise TelemetryMeasurementError(
            "telemetry Time must start at or after zero"
        )
    intervals = [
        current - previous
        for previous, current in zip(time_us, time_us[1:], strict=False)
    ]
    if any(interval <= 0 for interval in intervals):
        raise TelemetryMeasurementError(
            "telemetry Time values must be strictly increasing"
        )

    duration_us = time_us[-1] - time_us[0]
    median_interval_us = (
        int(round(median(intervals))) if intervals else 0
    )
    p95_interval_us = _nearest_rank_percentile(intervals, 0.95)
    samples_per_second = (
        round((len(frame) - 1) / (duration_us / 1_000_000), 3)
        if duration_us > 0 and len(frame) > 1
        else 0.0
    )
    memory_bytes = int(frame.memory_usage(index=True, deep=True).sum())

    return TelemetryFrameMeasurement(
        identity=identity,
        sample_count=len(frame),
        duration_us=duration_us,
        median_interval_us=median_interval_us,
        p95_interval_us=p95_interval_us,
        samples_per_second=samples_per_second,
        memory_bytes=memory_bytes,
        estimated_postgres_bytes=(
            len(frame) * ESTIMATED_POSTGRES_BYTES_PER_SAMPLE
        ),
        channels={
            channel: TelemetryChannelMeasurement(
                present=channel in frame.columns,
                non_null_samples=(
                    int(frame[channel].notna().sum())
                    if channel in frame.columns
                    else 0
                ),
            )
            for channel in MEASURED_CHANNELS
        },
    )


def summarize_telemetry_measurements(
    measurements: list[TelemetryFrameMeasurement],
) -> TelemetryMeasurementSummary:
    if not measurements:
        raise TelemetryMeasurementError(
            "at least one telemetry measurement is required"
        )
    sample_counts = [measurement.sample_count for measurement in measurements]
    return TelemetryMeasurementSummary(
        frame_count=len(measurements),
        total_samples=sum(sample_counts),
        average_samples_per_lap=round(sum(sample_counts) / len(sample_counts)),
        median_samples_per_lap=round(median(sample_counts)),
        total_memory_bytes=sum(
            measurement.memory_bytes for measurement in measurements
        ),
        estimated_postgres_bytes=sum(
            measurement.estimated_postgres_bytes
            for measurement in measurements
        ),
    )


def projected_postgres_storage_bytes(
    *,
    lap_count: int,
    samples_per_lap: int,
) -> int:
    if lap_count < 0 or samples_per_lap < 0:
        raise TelemetryMeasurementError(
            "projection inputs must be non-negative"
        )
    return (
        lap_count
        * samples_per_lap
        * ESTIMATED_POSTGRES_BYTES_PER_SAMPLE
    )


def _nearest_rank_percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[rank - 1]
