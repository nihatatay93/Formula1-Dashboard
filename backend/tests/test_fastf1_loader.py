import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import pytest

from app.ingestion import fastf1_loader
from app.ingestion.fastf1_loader import (
    FastF1LoaderConfigurationError,
    FastF1RateLimitError,
    FastF1SessionLoader,
    FastF1SessionLoadError,
    FastF1SessionRequest,
    create_fastf1_session_loader,
    serialized_fastf1_access,
)


class FakeFastF1Session:
    def __init__(self, name: str = "Race") -> None:
        self.name = name
        self.results = pd.DataFrame([{"DriverId": "test_driver"}])
        self.laps = pd.DataFrame([{"LapNumber": 1.0}])
        self.load_calls: list[dict[str, bool]] = []

    def load(self, **options: bool) -> None:
        self.load_calls.append(options)


class FakeRequestBudget:
    def __init__(self) -> None:
        self.reservations = 0

    def reserve(self) -> None:
        self.reservations += 1


class MalformedTyreInfoSession(FakeFastF1Session):
    def __init__(self) -> None:
        super().__init__()
        self.repaired_tyre_info: pd.DataFrame | None = None

    def _Session__fix_tyre_info(
        self,
        data: pd.DataFrame,
        stint_split_times: list[pd.Timedelta],
    ) -> pd.DataFrame:
        first_time = data["Time"].iloc[0]
        bracket_count = len(stint_split_times) + 2
        first_stints = data.loc[
            data["Time"] == first_time,
            "Stint",
        ].unique()
        if any(int(stint) >= bracket_count for stint in first_stints):
            raise IndexError("stint bracket is missing")
        self.repaired_tyre_info = data
        return data

    def load(self, **options: bool) -> None:
        first_time = pd.to_timedelta(1, unit="ms")
        data = pd.DataFrame(
            {
                "Time": [first_time, first_time],
                "Stint": [0, 3],
            }
        )
        self._Session__fix_tyre_info(
            data,
            [pd.to_timedelta(20, unit="min")],
        )
        super().load(**options)


@pytest.fixture(autouse=True)
def reset_active_cache_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastf1_loader, "_active_cache_path", None)


def test_loads_one_session_with_required_cache_and_data_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "fastf1-cache"
    session = FakeFastF1Session()
    cache_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    get_session_calls: list[tuple[Any, ...]] = []

    def enable_cache(*args: Any, **kwargs: Any) -> None:
        cache_calls.append((args, kwargs))

    def get_session(*args: Any) -> FakeFastF1Session:
        get_session_calls.append(args)
        return session

    monkeypatch.setattr(fastf1_loader.fastf1.Cache, "enable_cache", enable_cache)
    monkeypatch.setattr(fastf1_loader.fastf1, "get_session", get_session)

    request = FastF1SessionRequest(
        season_year=2024,
        round_number=1,
        session_identifier=" Race ",
    )
    loaded = FastF1SessionLoader(cache_path).load(request)

    assert cache_path.is_dir()
    assert cache_calls == [
        (
            (str(cache_path),),
            {
                "ignore_version": False,
                "force_renew": False,
                "use_requests_cache": True,
            },
        )
    ]
    assert get_session_calls == [(2024, 1, "Race")]
    assert session.load_calls == [
        {
            "laps": True,
            "telemetry": False,
            "weather": False,
            "messages": True,
        }
    ]
    assert loaded.request is request
    assert loaded.session_name == "Race"
    assert loaded.results is session.results
    assert loaded.laps is session.laps


def test_reuses_cache_activation_for_repeated_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_calls: list[str] = []
    sessions = [FakeFastF1Session(), FakeFastF1Session("Qualifying")]

    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda cache_path, **_: cache_calls.append(cache_path),
    )
    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        lambda *_: sessions.pop(0),
    )

    loader = FastF1SessionLoader(tmp_path / "cache")
    loader.load(FastF1SessionRequest(2024, 1, "Race"))
    loader.load(FastF1SessionRequest(2024, 1, "Qualifying"))

    assert cache_calls == [str(tmp_path / "cache")]


def test_repairs_bunched_stints_outside_fastf1_timing_brackets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MalformedTyreInfoSession()
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        lambda *_: session,
    )

    FastF1SessionLoader(tmp_path / "cache").load(
        FastF1SessionRequest(2018, 14, "Race")
    )

    assert session.repaired_tyre_info is not None
    repaired = session.repaired_tyre_info
    assert repaired.loc[repaired["Stint"] == 0, "Time"].item() == (
        pd.to_timedelta(1, unit="ms")
    )
    assert repaired.loc[repaired["Stint"] == 3, "Time"].item() == (
        pd.to_timedelta(86_400_001, unit="ms")
    )


def test_switching_cache_paths_reactivates_fastf1_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_calls: list[str] = []
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda cache_path, **_: cache_calls.append(cache_path),
    )
    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        lambda *_: FakeFastF1Session(),
    )

    first_path = tmp_path / "first-cache"
    second_path = tmp_path / "second-cache"
    FastF1SessionLoader(first_path).load(FastF1SessionRequest(2024, 1, "Race"))
    FastF1SessionLoader(second_path).load(FastF1SessionRequest(2024, 1, "Race"))

    assert cache_calls == [str(first_path), str(second_path)]


def test_factory_uses_environment_cache_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "environment-cache"
    monkeypatch.setenv("FASTF1_CACHE_PATH", str(cache_path))

    loader = create_fastf1_session_loader()

    assert loader.cache_path == cache_path


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"season_year": 2017, "round_number": 1, "session_identifier": "Race"}, "2018"),
        ({"season_year": True, "round_number": 1, "session_identifier": "Race"}, "2018"),
        ({"season_year": 2024, "round_number": 0, "session_identifier": "Race"}, "positive"),
        ({"season_year": 2024, "round_number": True, "session_identifier": "Race"}, "positive"),
        ({"season_year": 2024, "round_number": 1, "session_identifier": " "}, "non-empty"),
    ],
)
def test_rejects_invalid_session_requests(
    arguments: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(FastF1LoaderConfigurationError, match=message):
        FastF1SessionRequest(**arguments)


def test_rejects_missing_or_relative_cache_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASTF1_CACHE_PATH", raising=False)

    with pytest.raises(FastF1LoaderConfigurationError, match="required"):
        create_fastf1_session_loader()
    with pytest.raises(FastF1LoaderConfigurationError, match="absolute"):
        FastF1SessionLoader("relative/cache")


def test_wraps_fastf1_load_failures_without_returning_partial_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )

    def fail_session(*_args: Any) -> None:
        raise ConnectionError("upstream unavailable")

    monkeypatch.setattr(fastf1_loader.fastf1, "get_session", fail_session)

    with pytest.raises(FastF1SessionLoadError, match="2024 round 1") as error:
        FastF1SessionLoader(tmp_path / "cache").load(
            FastF1SessionRequest(2024, 1, "Race")
        )

    assert isinstance(error.value.__cause__, ConnectionError)


def test_preserves_fastf1_rate_limit_as_a_distinct_retry_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )

    def rate_limited(*_args: Any) -> None:
        raise fastf1_loader.fastf1.exceptions.RateLimitExceededError

    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        rate_limited,
    )

    with pytest.raises(FastF1RateLimitError) as error:
        FastF1SessionLoader(tmp_path / "cache").load(
            FastF1SessionRequest(2024, 1, "Race")
        )

    assert isinstance(
        error.value.__cause__,
        fastf1_loader.fastf1.exceptions.RateLimitExceededError,
    )


def test_reserves_budget_only_inside_serialized_fastf1_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_loader,
        "_ORIGINAL_FASTF1_SEND",
        lambda *_args, **_kwargs: "sent",
    )
    budget = FakeRequestBudget()
    loader = FastF1SessionLoader(
        tmp_path / "cache",
        request_budget=budget,
    )

    assert fastf1_loader._budgeted_fastf1_send(
        object(),
        object(),
    ) == "sent"
    assert budget.reservations == 0

    with serialized_fastf1_access(loader):
        assert fastf1_loader._budgeted_fastf1_send(
            object(),
            object(),
        ) == "sent"

    assert budget.reservations == 1


def test_rejects_loaded_sessions_without_required_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeFastF1Session()
    session.results = None
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        lambda *_: session,
    )

    with pytest.raises(FastF1SessionLoadError, match="results table"):
        FastF1SessionLoader(tmp_path / "cache").load(
            FastF1SessionRequest(2024, 1, "Race")
        )


def test_serializes_concurrent_fastf1_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_lock = Lock()
    active_loads = 0
    maximum_active_loads = 0

    class ConcurrentSession(FakeFastF1Session):
        def load(self, **options: bool) -> None:
            nonlocal active_loads, maximum_active_loads
            with state_lock:
                active_loads += 1
                maximum_active_loads = max(maximum_active_loads, active_loads)
            time.sleep(0.02)
            with state_lock:
                active_loads -= 1
            super().load(**options)

    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_loader.fastf1,
        "get_session",
        lambda *_: ConcurrentSession(),
    )

    loader = FastF1SessionLoader(tmp_path / "cache")
    requests = (
        FastF1SessionRequest(2024, 1, "Race"),
        FastF1SessionRequest(2024, 2, "Race"),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        loaded_sessions = list(executor.map(loader.load, requests))

    assert len(loaded_sessions) == 2
    assert maximum_active_loads == 1
