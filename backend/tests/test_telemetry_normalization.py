from datetime import timedelta

import pandas as pd
import pytest

from app.ingestion.telemetry_normalization import (
    TelemetryNormalizationError,
    normalize_fastf1_telemetry,
)


def test_normalizes_supported_channels_with_deterministic_indices() -> None:
    frame = pd.DataFrame(
        {
            "Time": [
                timedelta(milliseconds=10),
                timedelta(milliseconds=250),
            ],
            "SessionTime": [
                timedelta(seconds=100),
                timedelta(seconds=100, milliseconds=240),
            ],
            "Distance": [1.25, 42.5],
            "RelativeDistance": [0.01, 0.2],
            "Speed": [75.5, 287.2],
            "RPM": [5000, 12100],
            "nGear": [1, 7],
            "Throttle": [20.0, 100.0],
            "Brake": [True, False],
            "DRS": [0, 10],
            "X": [10, 20],
            "Y": [-5, -4],
            "Z": [1, 2],
        }
    )

    samples = normalize_fastf1_telemetry(frame)

    assert [sample.sample_index for sample in samples] == [0, 1]
    assert samples[0].lap_time_us == 10_000
    assert samples[1].session_time_us == 100_240_000
    assert samples[1].speed_kph == pytest.approx(287.2)
    assert samples[1].gear == 7
    assert samples[1].brake is False


def test_optional_channels_are_nullable() -> None:
    samples = normalize_fastf1_telemetry(
        pd.DataFrame({"Time": [timedelta(milliseconds=1)]})
    )

    assert samples[0].distance_m is None
    assert samples[0].x is None


@pytest.mark.parametrize(
    "frame,message",
    [
        (pd.DataFrame(), "must not be empty"),
        (pd.DataFrame({"Speed": [1]}), "requires Time"),
        (
            pd.DataFrame(
                {
                    "Time": [
                        timedelta(milliseconds=2),
                        timedelta(milliseconds=2),
                    ]
                }
            ),
            "strictly increasing",
        ),
        (
            pd.DataFrame(
                {
                    "Time": [timedelta(milliseconds=1)],
                    "Throttle": [101],
                }
            ),
            "above range",
        ),
    ],
)
def test_rejects_malformed_or_out_of_range_telemetry(
    frame: pd.DataFrame,
    message: str,
) -> None:
    with pytest.raises(TelemetryNormalizationError, match=message):
        normalize_fastf1_telemetry(frame)
