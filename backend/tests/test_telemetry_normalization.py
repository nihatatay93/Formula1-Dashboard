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
                    # Far outside the allowance: a corrupt snapshot, not the
                    # ECU reporting a little over full throttle.
                    "Throttle": [150],
                }
            ),
            "above range",
        ),
        (
            pd.DataFrame(
                {
                    "Time": [timedelta(milliseconds=1)],
                    "Distance": [-500.0],
                }
            ),
            "below range",
        ),
    ],
)
def test_rejects_malformed_or_out_of_range_telemetry(
    frame: pd.DataFrame,
    message: str,
) -> None:
    with pytest.raises(TelemetryNormalizationError, match=message):
        normalize_fastf1_telemetry(frame)


def _frame(**overrides: object) -> pd.DataFrame:
    columns: dict[str, object] = {
        "Time": [timedelta(milliseconds=10), timedelta(milliseconds=250)],
        "Speed": [75.5, 287.2],
        "RPM": [5000, 12100],
        "nGear": [1, 7],
        "Throttle": [20.0, 100.0],
        "Brake": [True, False],
        "DRS": [0, 10],
    }
    columns.update(overrides)
    return pd.DataFrame(columns)


def test_interpolated_rpm_is_rounded_rather_than_rejected() -> None:
    """FastF1 merges car and position data, so RPM arrives fractional.

    Roughly half the samples in a real lap carry a fractional RPM, so rejecting
    them rejected the lap — and with it every lap.
    """
    samples = normalize_fastf1_telemetry(
        _frame(RPM=[10785.091675733334, 10972.558350933334])
    )

    assert [sample.rpm for sample in samples] == [10785, 10973]


def test_a_fractional_gear_is_still_a_corrupt_snapshot() -> None:
    # Gear is a discrete state the upstream carries forward, never interpolates.
    with pytest.raises(TelemetryNormalizationError):
        normalize_fastf1_telemetry(_frame(nGear=[1.5, 7]))


def test_a_fractional_drs_is_still_a_corrupt_snapshot() -> None:
    with pytest.raises(TelemetryNormalizationError):
        normalize_fastf1_telemetry(_frame(DRS=[0.5, 10]))


def test_a_negative_rpm_is_still_out_of_range() -> None:
    with pytest.raises(TelemetryNormalizationError):
        normalize_fastf1_telemetry(_frame(RPM=[-1.4, 12100]))


def test_slightly_over_full_throttle_is_clamped_not_rejected() -> None:
    """The ECU really does report over 100%; 104 was observed on one lap.

    Refusing the sample discarded the whole lap, which is how telemetry came
    to fail for most laps while succeeding for others.
    """
    samples = normalize_fastf1_telemetry(_frame(Throttle=[104.0, 100.0]))

    assert [sample.throttle_percent for sample in samples] == [100.0, 100.0]


def test_a_marginally_negative_distance_is_clamped_not_rejected() -> None:
    # FastF1 integrates distance from speed, so the first sample of a lap can
    # land a few millimetres below zero.
    samples = normalize_fastf1_telemetry(
        _frame(Distance=[-0.0049, 12.5], RelativeDistance=[-0.0000009, 0.4])
    )

    assert samples[0].distance_m == 0.0
    assert samples[0].relative_distance == 0.0
    assert samples[1].distance_m == pytest.approx(12.5)


def test_an_excursion_beyond_the_allowance_is_still_a_corrupt_snapshot() -> None:
    with pytest.raises(TelemetryNormalizationError):
        normalize_fastf1_telemetry(_frame(Throttle=[130.0, 100.0]))

    with pytest.raises(TelemetryNormalizationError):
        normalize_fastf1_telemetry(_frame(Distance=[-50.0, 12.5]))
