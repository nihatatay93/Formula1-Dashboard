"""Validated settings for the ephemeral live-timing path.

Live timing never writes to the sporting-data schema, so these settings govern
only the disposable per-session JSONL logs and their retention. See
``docs/LIVE_TIMING_DESIGN.md``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

DEFAULT_LOG_DIRECTORY = "/live-sessions"


class LiveTimingPolicyError(ValueError):
    """Base error for invalid live-timing policy input."""


@dataclass(frozen=True, slots=True)
class LiveTimingSettings:
    log_directory: str = DEFAULT_LOG_DIRECTORY
    retention_days: int = 7
    retention_sweep_interval_seconds: int = 3_600
    max_log_bytes: int = 536_870_912
    max_directory_bytes: int = 5_368_709_120

    def __post_init__(self) -> None:
        if not isinstance(self.log_directory, str) or not self.log_directory.strip():
            raise LiveTimingPolicyError("log_directory must be a non-empty string")
        _positive_integer(self.retention_days, "retention_days")
        _positive_integer(
            self.retention_sweep_interval_seconds,
            "retention_sweep_interval_seconds",
        )
        _positive_integer(self.max_log_bytes, "max_log_bytes")
        _positive_integer(self.max_directory_bytes, "max_directory_bytes")
        if self.max_log_bytes > self.max_directory_bytes:
            raise LiveTimingPolicyError(
                "max_log_bytes must not exceed max_directory_bytes"
            )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> LiveTimingSettings:
        values = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            log_directory=values.get(
                "LIVE_TIMING_LOG_DIRECTORY",
                defaults.log_directory,
            ),
            retention_days=_environment_integer(
                values,
                "LIVE_TIMING_RETENTION_DAYS",
                defaults.retention_days,
            ),
            retention_sweep_interval_seconds=_environment_integer(
                values,
                "LIVE_TIMING_RETENTION_SWEEP_INTERVAL_SECONDS",
                defaults.retention_sweep_interval_seconds,
            ),
            max_log_bytes=_environment_integer(
                values,
                "LIVE_TIMING_MAX_LOG_BYTES",
                defaults.max_log_bytes,
            ),
            max_directory_bytes=_environment_integer(
                values,
                "LIVE_TIMING_MAX_DIRECTORY_BYTES",
                defaults.max_directory_bytes,
            ),
        )

    @property
    def retention(self) -> timedelta:
        return timedelta(days=self.retention_days)

    @property
    def retention_sweep_interval(self) -> timedelta:
        return timedelta(seconds=self.retention_sweep_interval_seconds)


def _positive_integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiveTimingPolicyError(f"{field} must be a positive integer")


def _environment_integer(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        raise LiveTimingPolicyError(f"{name} must be an integer") from None
