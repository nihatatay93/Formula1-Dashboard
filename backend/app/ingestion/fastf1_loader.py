from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import fastf1
from pandas import DataFrame

MINIMUM_ARCHIVE_YEAR = 2018

_LOAD_LOCK = Lock()
_active_cache_path: Path | None = None


class FastF1LoaderError(RuntimeError):
    """Base error for FastF1 loader configuration and execution failures."""


class FastF1LoaderConfigurationError(FastF1LoaderError):
    """Raised when the cache or request configuration is invalid."""


class FastF1SessionLoadError(FastF1LoaderError):
    """Raised when FastF1 cannot produce a usable loaded session."""


@dataclass(frozen=True, slots=True)
class FastF1SessionRequest:
    season_year: int
    round_number: int
    session_identifier: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.season_year, bool)
            or not isinstance(self.season_year, int)
            or self.season_year < MINIMUM_ARCHIVE_YEAR
        ):
            raise FastF1LoaderConfigurationError(
                f"season_year must be at least {MINIMUM_ARCHIVE_YEAR}"
            )
        if (
            isinstance(self.round_number, bool)
            or not isinstance(self.round_number, int)
            or self.round_number < 1
        ):
            raise FastF1LoaderConfigurationError(
                "round_number must be a positive integer"
            )
        if (
            not isinstance(self.session_identifier, str)
            or not self.session_identifier.strip()
        ):
            raise FastF1LoaderConfigurationError(
                "session_identifier must be a non-empty string"
            )


@dataclass(frozen=True, slots=True)
class LoadedFastF1Session:
    request: FastF1SessionRequest
    session_name: str
    results: DataFrame
    laps: DataFrame


class FastF1SessionLoader:
    """Load one FastF1 session at a time through a persistent process cache."""

    def __init__(self, cache_path: str | Path) -> None:
        candidate = Path(cache_path)
        if not candidate.is_absolute():
            raise FastF1LoaderConfigurationError(
                "FastF1 cache path must be absolute"
            )
        self.cache_path = candidate.resolve(strict=False)

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        with _LOAD_LOCK:
            self._enable_cache()
            try:
                session = fastf1.get_session(
                    request.season_year,
                    request.round_number,
                    request.session_identifier.strip(),
                )
                session.load(
                    laps=True,
                    telemetry=False,
                    weather=False,
                    messages=True,
                )
            except Exception as error:
                raise FastF1SessionLoadError(
                    "FastF1 failed to load "
                    f"{request.season_year} round {request.round_number} "
                    f"session {request.session_identifier!r}"
                ) from error

            session_name = getattr(session, "name", None)
            results = getattr(session, "results", None)
            laps = getattr(session, "laps", None)
            if not isinstance(session_name, str) or not session_name.strip():
                raise FastF1SessionLoadError(
                    "FastF1 loaded a session without a usable session name"
                )
            if not isinstance(results, DataFrame):
                raise FastF1SessionLoadError(
                    "FastF1 loaded a session without a results table"
                )
            if not isinstance(laps, DataFrame):
                raise FastF1SessionLoadError(
                    "FastF1 loaded a session without a laps table"
                )

            return LoadedFastF1Session(
                request=request,
                session_name=session_name.strip(),
                results=results,
                laps=laps,
            )

    def _enable_cache(self) -> None:
        global _active_cache_path

        try:
            self.cache_path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FastF1LoaderConfigurationError(
                f"FastF1 cache directory cannot be created: {self.cache_path}"
            ) from error

        if not self.cache_path.is_dir():
            raise FastF1LoaderConfigurationError(
                f"FastF1 cache path is not a directory: {self.cache_path}"
            )

        if _active_cache_path == self.cache_path:
            return

        try:
            fastf1.Cache.enable_cache(
                str(self.cache_path),
                ignore_version=False,
                force_renew=False,
                use_requests_cache=True,
            )
        except Exception as error:
            raise FastF1LoaderConfigurationError(
                f"FastF1 cache could not be enabled: {self.cache_path}"
            ) from error

        _active_cache_path = self.cache_path


def create_fastf1_session_loader(
    cache_path: str | Path | None = None,
) -> FastF1SessionLoader:
    configured_path = cache_path
    if configured_path is None:
        configured_path = os.environ.get("FASTF1_CACHE_PATH")
    if configured_path is None or not str(configured_path).strip():
        raise FastF1LoaderConfigurationError(
            "FASTF1_CACHE_PATH is required"
        )
    return FastF1SessionLoader(configured_path)
