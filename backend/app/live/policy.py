"""Validated settings for the ephemeral live-timing path.

Live timing never writes to the sporting-data schema, so these settings govern
only the disposable per-session JSONL logs and their retention. See
``docs/LIVE_TIMING_DESIGN.md``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

DEFAULT_LOG_DIRECTORY = "/live-sessions"
DEFAULT_TOKEN_PATH = "/live-auth/f1-token.json"


class LiveTimingPolicyError(ValueError):
    """Base error for invalid live-timing policy input."""


@dataclass(frozen=True, slots=True)
class LiveTimingSettings:
    log_directory: str = DEFAULT_LOG_DIRECTORY
    retention_days: int = 7
    retention_sweep_interval_seconds: int = 3_600
    max_log_bytes: int = 536_870_912
    max_directory_bytes: int = 5_368_709_120
    reconnect_base_seconds: int = 2
    reconnect_multiplier: int = 2
    reconnect_cap_seconds: int = 60
    reconnect_jitter_min_ratio: float = 0.5
    #: When set, live sessions are driven by a recorded session instead of a
    #: live upstream connection. Intended for development and demonstration.
    replay_path: str | None = None
    replay_speed: float = 10.0
    #: Where the F1 TV login-session token is stored, and how long it is trusted
    #: when the token carries no usable expiry claim of its own.
    token_path: str = DEFAULT_TOKEN_PATH
    token_ttl_hours: int = 96
    #: Port the companion extension is told to post the token back to.
    auth_callback_port: int = 8000
    #: Whether to expose the root ``/auth`` route the FastF1 companion
    #: extension posts to. Off by default: the extension posts to
    #: ``http://localhost:<port>`` on the reader's own machine, so the route is
    #: only reachable — and only meaningful — when the API runs there. On a
    #: deployed instance it can never be used, and exposing it would publish a
    #: token-accepting endpoint with permissive CORS for no benefit.
    companion_enabled: bool = False

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
        _positive_integer(self.reconnect_base_seconds, "reconnect_base_seconds")
        _positive_integer(self.reconnect_multiplier, "reconnect_multiplier")
        _positive_integer(self.reconnect_cap_seconds, "reconnect_cap_seconds")
        if (
            isinstance(self.reconnect_jitter_min_ratio, bool)
            or not isinstance(self.reconnect_jitter_min_ratio, int | float)
            or not math.isfinite(self.reconnect_jitter_min_ratio)
            or not 0 <= self.reconnect_jitter_min_ratio <= 1
        ):
            raise LiveTimingPolicyError(
                "reconnect_jitter_min_ratio must be between zero and one"
            )
        if self.reconnect_base_seconds > self.reconnect_cap_seconds:
            raise LiveTimingPolicyError(
                "reconnect_base_seconds must not exceed reconnect_cap_seconds"
            )
        if not isinstance(self.token_path, str) or not self.token_path.strip():
            raise LiveTimingPolicyError("token_path must be a non-empty string")
        _positive_integer(self.token_ttl_hours, "token_ttl_hours")
        _positive_integer(self.auth_callback_port, "auth_callback_port")
        if self.auth_callback_port > 65535:
            raise LiveTimingPolicyError("auth_callback_port must be a valid port")
        if self.replay_path is not None and not str(self.replay_path).strip():
            raise LiveTimingPolicyError(
                "replay_path must be a non-empty string when set"
            )
        if (
            isinstance(self.replay_speed, bool)
            or not isinstance(self.replay_speed, int | float)
            or not math.isfinite(self.replay_speed)
            or self.replay_speed <= 0
        ):
            raise LiveTimingPolicyError("replay_speed must be a positive number")

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
            replay_path=values.get("LIVE_TIMING_REPLAY_PATH") or None,
            replay_speed=_environment_float(
                values,
                "LIVE_TIMING_REPLAY_SPEED",
                defaults.replay_speed,
            ),
            companion_enabled=_environment_flag(
                values,
                "LIVE_TIMING_COMPANION_ENABLED",
                defaults.companion_enabled,
            ),
            token_path=values.get("LIVE_TIMING_TOKEN_PATH", defaults.token_path),
            token_ttl_hours=_environment_integer(
                values,
                "LIVE_TIMING_TOKEN_TTL_HOURS",
                defaults.token_ttl_hours,
            ),
            auth_callback_port=_environment_integer(
                values,
                "LIVE_TIMING_AUTH_CALLBACK_PORT",
                defaults.auth_callback_port,
            ),
        )

    @property
    def retention(self) -> timedelta:
        return timedelta(days=self.retention_days)

    @property
    def retention_sweep_interval(self) -> timedelta:
        return timedelta(seconds=self.retention_sweep_interval_seconds)

    @property
    def token_ttl(self) -> timedelta:
        return timedelta(hours=self.token_ttl_hours)


def calculate_reconnect_delay(
    *,
    attempt: int,
    jitter_fraction: float,
    settings: LiveTimingSettings,
) -> timedelta:
    """Equal-jitter reconnect delay for an unbounded number of attempts.

    This deliberately does not reuse ``calculate_retry_schedule`` from
    ``app.ingestion.runtime_policy``. That function shares the same equal-jitter
    formula but enforces the archive retry budget and raises once attempts are
    exhausted. Live reconnects are unbounded and must never consume a session's
    archive retry budget, so only the formula is shared, not the semantics.
    """
    _positive_integer(attempt, "attempt")
    if (
        isinstance(jitter_fraction, bool)
        or not isinstance(jitter_fraction, int | float)
        or not math.isfinite(jitter_fraction)
        or not 0 <= jitter_fraction <= 1
    ):
        raise LiveTimingPolicyError(
            "jitter_fraction must be between zero and one"
        )

    nominal_seconds = min(
        settings.reconnect_base_seconds
        * settings.reconnect_multiplier ** (attempt - 1),
        settings.reconnect_cap_seconds,
    )
    minimum_seconds = nominal_seconds * settings.reconnect_jitter_min_ratio
    delay_seconds = minimum_seconds + (
        nominal_seconds - minimum_seconds
    ) * jitter_fraction
    return timedelta(seconds=delay_seconds)


def _positive_integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiveTimingPolicyError(f"{field} must be a positive integer")


def _environment_flag(
    values: Mapping[str, str],
    key: str,
    default: bool,
) -> bool:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _environment_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        raise LiveTimingPolicyError(f"{name} must be a number") from None
