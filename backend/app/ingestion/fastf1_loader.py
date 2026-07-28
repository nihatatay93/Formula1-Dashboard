from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import fastf1
import pandas as pd
from fastf1 import req as fastf1_req
from pandas import DataFrame

from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)

MINIMUM_ARCHIVE_YEAR = 2018

_LOAD_LOCK = Lock()
_active_cache_path: Path | None = None


class FastF1RequestBudgetProtocol(Protocol):
    def reserve(self) -> None: ...


_ACTIVE_REQUEST_BUDGET: ContextVar[
    FastF1RequestBudgetProtocol | None
] = ContextVar("fastf1_request_budget", default=None)
_ORIGINAL_FASTF1_SEND = fastf1_req._SessionWithRateLimiting.send


def _budgeted_fastf1_send(
    request_session: object,
    request: object,
    **kwargs: Any,
) -> object:
    budget = _ACTIVE_REQUEST_BUDGET.get()
    if budget is not None:
        budget.reserve()
    return _ORIGINAL_FASTF1_SEND(request_session, request, **kwargs)


fastf1_req._SessionWithRateLimiting.send = _budgeted_fastf1_send


class FastF1LoaderError(RuntimeError):
    """Base error for FastF1 loader configuration and execution failures."""


class FastF1LoaderConfigurationError(FastF1LoaderError):
    """Raised when the cache or request configuration is invalid."""


class FastF1SessionLoadError(FastF1LoaderError):
    """Raised when FastF1 cannot produce a usable loaded session."""


class FastF1RateLimitError(FastF1SessionLoadError):
    """Raised when FastF1 reports that its upstream request budget is exhausted."""


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


@dataclass(frozen=True, slots=True)
class FastF1TelemetryRequest:
    season_year: int
    round_number: int
    session_identifier: str
    driver_identifier: str
    lap_number: int

    def __post_init__(self) -> None:
        FastF1SessionRequest(
            season_year=self.season_year,
            round_number=self.round_number,
            session_identifier=self.session_identifier,
        )
        if (
            not isinstance(self.driver_identifier, str)
            or not self.driver_identifier.strip()
        ):
            raise FastF1LoaderConfigurationError(
                "driver_identifier must be a non-empty string"
            )
        if (
            isinstance(self.lap_number, bool)
            or not isinstance(self.lap_number, int)
            or self.lap_number < 1
        ):
            raise FastF1LoaderConfigurationError(
                "lap_number must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class LoadedFastF1Telemetry:
    request: FastF1TelemetryRequest
    session_name: str
    telemetry: DataFrame


class FastF1SessionLoader:
    """Load one FastF1 session at a time through a persistent process cache."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        request_budget: FastF1RequestBudgetProtocol | None = None,
    ) -> None:
        candidate = Path(cache_path)
        if not candidate.is_absolute():
            raise FastF1LoaderConfigurationError(
                "FastF1 cache path must be absolute"
            )
        self.cache_path = candidate.resolve(strict=False)
        self.request_budget = request_budget

    def load(self, request: FastF1SessionRequest) -> LoadedFastF1Session:
        with serialized_fastf1_access(self):
            try:
                session = fastf1.get_session(
                    request.season_year,
                    request.round_number,
                    request.session_identifier.strip(),
                )
                _install_tyre_info_compatibility(session)
                session.load(
                    laps=True,
                    telemetry=False,
                    weather=False,
                    messages=True,
                )
            except FastF1RequestBudgetExhaustedError:
                raise
            except fastf1.exceptions.RateLimitExceededError as error:
                raise FastF1RateLimitError(
                    "FastF1 archive request rate limit was reached"
                ) from error
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

    def load_telemetry(
        self,
        request: FastF1TelemetryRequest,
    ) -> LoadedFastF1Telemetry:
        """Load one exact driver's lap through the serialized cache boundary."""

        with serialized_fastf1_access(self):
            try:
                session = fastf1.get_session(
                    request.season_year,
                    request.round_number,
                    request.session_identifier.strip(),
                )
                _install_tyre_info_compatibility(session)
                session.load(
                    laps=True,
                    telemetry=True,
                    weather=False,
                    messages=False,
                )
                laps = session.laps.pick_drivers(
                    request.driver_identifier.strip()
                ).pick_laps(request.lap_number)
                if len(laps) != 1:
                    raise FastF1SessionLoadError(
                        "FastF1 did not resolve exactly one requested lap"
                    )
                lap = laps.iloc[0]
                try:
                    telemetry = lap.get_telemetry(frequency="original")
                except KeyError:
                    telemetry = (
                        lap.get_car_data()
                        .add_distance()
                        .add_relative_distance()
                    )
            except FastF1RequestBudgetExhaustedError:
                raise
            except FastF1SessionLoadError:
                raise
            except fastf1.exceptions.RateLimitExceededError as error:
                raise FastF1RateLimitError(
                    "FastF1 telemetry request rate limit was reached"
                ) from error
            except Exception as error:
                raise FastF1SessionLoadError(
                    "FastF1 failed to load requested lap telemetry"
                ) from error

            session_name = getattr(session, "name", None)
            if not isinstance(session_name, str) or not session_name.strip():
                raise FastF1SessionLoadError(
                    "FastF1 loaded telemetry without a usable session name"
                )
            if not isinstance(telemetry, DataFrame) or telemetry.empty:
                raise FastF1SessionLoadError(
                    "FastF1 loaded an empty telemetry table"
                )
            return LoadedFastF1Telemetry(
                request=request,
                session_name=session_name.strip(),
                telemetry=telemetry,
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


def _install_tyre_info_compatibility(session: object) -> None:
    """Contain a pinned FastF1 3.8.3 malformed-stint parser defect."""

    attribute = "_Session__fix_tyre_info"
    original = getattr(session, attribute, None)
    if not callable(original):
        return

    def compatible_tyre_info(
        data: DataFrame,
        stint_split_times: list[Any],
    ) -> DataFrame:
        pristine = data.copy()
        try:
            return original(data, stint_split_times)
        except IndexError:
            repaired = _repair_bunched_stint_timestamps(
                pristine,
                stint_split_times,
            )
            if repaired is pristine:
                raise
            return original(repaired, stint_split_times)

    setattr(session, attribute, compatible_tyre_info)


def _repair_bunched_stint_timestamps(
    data: DataFrame,
    stint_split_times: list[Any],
) -> DataFrame:
    required_columns = {"Time", "Stint"}
    if (
        data.empty
        or not stint_split_times
        or not required_columns.issubset(data.columns)
    ):
        return data

    first_time = data["Time"].iloc[0]
    first_time_mask = data["Time"] == first_time
    first_stints = data.loc[first_time_mask, "Stint"].dropna().unique()
    bracket_count = len(stint_split_times) + 2
    invalid_stints: list[Any] = []

    for stint in first_stints:
        if isinstance(stint, bool):
            return data
        try:
            normalized_stint = int(stint)
        except (TypeError, ValueError, OverflowError):
            return data
        if normalized_stint != stint or normalized_stint < 0:
            return data
        if normalized_stint >= bracket_count:
            invalid_stints.append(stint)

    if not invalid_stints:
        return data

    repaired = data.copy()
    invalid_mask = first_time_mask & repaired["Stint"].isin(invalid_stints)
    repaired.loc[invalid_mask, "Time"] = pd.to_timedelta(
        86_400_001,
        unit="ms",
    )
    return repaired


@contextmanager
def serialized_fastf1_access(
    cache_client: FastF1SessionLoader,
) -> Iterator[None]:
    """Serialize use of FastF1's process-global persistent cache."""

    with _LOAD_LOCK:
        cache_client._enable_cache()
        token = _ACTIVE_REQUEST_BUDGET.set(cache_client.request_budget)
        try:
            yield
        finally:
            _ACTIVE_REQUEST_BUDGET.reset(token)


def create_fastf1_session_loader(
    cache_path: str | Path | None = None,
    *,
    request_budget: FastF1RequestBudgetProtocol | None = None,
) -> FastF1SessionLoader:
    configured_path = cache_path
    if configured_path is None:
        configured_path = os.environ.get("FASTF1_CACHE_PATH")
    if configured_path is None or not str(configured_path).strip():
        raise FastF1LoaderConfigurationError(
            "FASTF1_CACHE_PATH is required"
        )
    return FastF1SessionLoader(
        configured_path,
        request_budget=request_budget,
    )
