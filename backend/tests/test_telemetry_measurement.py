from __future__ import annotations

import pandas as pd
import pytest

from app.ingestion.telemetry_measurement import (
    ESTIMATED_POSTGRES_BYTES_PER_SAMPLE,
    TelemetryMeasurementError,
    TelemetryMeasurementIdentity,
    measure_telemetry_frame,
    projected_postgres_storage_bytes,
    summarize_telemetry_measurements,
)

IDENTITY = TelemetryMeasurementIdentity(
    season_year=2025,
    round_number=1,
    session_identifier="FP2",
    driver_identifier="NOR",
    lap_number=12,
)


def telemetry_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Time": pd.to_timedelta([0, 200, 400, 650], unit="ms"),
            "Speed": [100, 120, 150, 170],
            "RPM": [8_000, 9_000, None, 10_000],
            "nGear": [3, 4, 5, 6],
            "Throttle": [40.0, 60.0, 90.0, 100.0],
            "Brake": [True, False, False, False],
            "DRS": [0, 0, 8, 12],
            "Distance": [0.0, 30.0, 70.0, 115.0],
            "RelativeDistance": [0.0, 0.01, 0.02, 0.03],
            "X": [1, 2, 3, 4],
            "Y": [5, 6, 7, 8],
        }
    )


def test_measure_telemetry_frame_records_frequency_size_and_coverage() -> None:
    measurement = measure_telemetry_frame(
        telemetry_frame(),
        identity=IDENTITY,
    )

    assert measurement.sample_count == 4
    assert measurement.duration_us == 650_000
    assert measurement.median_interval_us == 200_000
    assert measurement.p95_interval_us == 250_000
    assert measurement.samples_per_second == 4.615
    assert measurement.memory_bytes > 0
    assert measurement.estimated_postgres_bytes == (
        4 * ESTIMATED_POSTGRES_BYTES_PER_SAMPLE
    )
    assert measurement.channels["RPM"].present is True
    assert measurement.channels["RPM"].non_null_samples == 3
    assert measurement.channels["Z"].present is False
    assert measurement.channels["Z"].non_null_samples == 0


def test_measurement_rejects_empty_missing_invalid_or_unordered_time() -> None:
    with pytest.raises(TelemetryMeasurementError, match="must not be empty"):
        measure_telemetry_frame(pd.DataFrame(), identity=IDENTITY)
    with pytest.raises(TelemetryMeasurementError, match="must contain"):
        measure_telemetry_frame(
            pd.DataFrame({"Speed": [100]}),
            identity=IDENTITY,
        )
    with pytest.raises(TelemetryMeasurementError, match="valid durations"):
        measure_telemetry_frame(
            pd.DataFrame({"Time": ["invalid"]}),
            identity=IDENTITY,
        )
    with pytest.raises(TelemetryMeasurementError, match="strictly increasing"):
        measure_telemetry_frame(
            pd.DataFrame(
                {"Time": pd.to_timedelta([0, 200, 200], unit="ms")}
            ),
            identity=IDENTITY,
        )


def test_summary_and_storage_projection_are_deterministic() -> None:
    first = measure_telemetry_frame(telemetry_frame(), identity=IDENTITY)
    base_frame = telemetry_frame()
    final_frame = base_frame.tail(1).copy()
    final_frame["Time"] = pd.to_timedelta([900], unit="ms")
    second_frame = pd.concat([base_frame, final_frame], ignore_index=True)
    second = measure_telemetry_frame(second_frame, identity=IDENTITY)

    summary = summarize_telemetry_measurements([first, second])

    assert summary.frame_count == 2
    assert summary.total_samples == 9
    assert summary.average_samples_per_lap == 4
    assert summary.median_samples_per_lap == 4
    assert summary.estimated_postgres_bytes == (
        9 * ESTIMATED_POSTGRES_BYTES_PER_SAMPLE
    )
    assert projected_postgres_storage_bytes(
        lap_count=58_002,
        samples_per_lap=400,
    ) == 3_712_128_000


def test_summary_and_projection_validate_inputs() -> None:
    with pytest.raises(TelemetryMeasurementError, match="at least one"):
        summarize_telemetry_measurements([])
    with pytest.raises(TelemetryMeasurementError, match="non-negative"):
        projected_postgres_storage_bytes(
            lap_count=-1,
            samples_per_lap=400,
        )
