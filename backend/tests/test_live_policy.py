from datetime import timedelta

import pytest

from app.live.policy import LiveTimingPolicyError, LiveTimingSettings


def test_defaults_expose_retention_and_sweep_intervals() -> None:
    settings = LiveTimingSettings()

    assert settings.retention_days == 7
    assert settings.retention == timedelta(days=7)
    assert settings.retention_sweep_interval == timedelta(hours=1)
    assert settings.max_log_bytes <= settings.max_directory_bytes


def test_environment_overrides_every_setting() -> None:
    settings = LiveTimingSettings.from_environment(
        {
            "LIVE_TIMING_LOG_DIRECTORY": "/tmp/live",
            "LIVE_TIMING_RETENTION_DAYS": "1",
            "LIVE_TIMING_RETENTION_SWEEP_INTERVAL_SECONDS": "60",
            "LIVE_TIMING_MAX_LOG_BYTES": "1024",
            "LIVE_TIMING_MAX_DIRECTORY_BYTES": "4096",
        }
    )

    assert settings.log_directory == "/tmp/live"
    assert settings.retention == timedelta(days=1)
    assert settings.retention_sweep_interval == timedelta(seconds=60)
    assert settings.max_log_bytes == 1024
    assert settings.max_directory_bytes == 4096


def test_missing_environment_values_fall_back_to_defaults() -> None:
    assert LiveTimingSettings.from_environment({}) == LiveTimingSettings()


@pytest.mark.parametrize(
    "overrides",
    [
        {"retention_days": 0},
        {"retention_days": -1},
        {"retention_days": True},
        {"retention_sweep_interval_seconds": 0},
        {"max_log_bytes": 0},
        {"max_directory_bytes": 0},
        {"log_directory": ""},
        {"log_directory": "   "},
    ],
)
def test_invalid_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(LiveTimingPolicyError):
        LiveTimingSettings(**overrides)


def test_per_log_cap_may_not_exceed_the_directory_cap() -> None:
    with pytest.raises(LiveTimingPolicyError, match="max_directory_bytes"):
        LiveTimingSettings(max_log_bytes=2048, max_directory_bytes=1024)


def test_non_integer_environment_value_is_rejected() -> None:
    with pytest.raises(LiveTimingPolicyError, match="must be an integer"):
        LiveTimingSettings.from_environment({"LIVE_TIMING_RETENTION_DAYS": "week"})
