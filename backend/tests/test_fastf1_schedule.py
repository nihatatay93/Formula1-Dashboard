import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from app.ingestion import fastf1_loader, fastf1_schedule
from app.ingestion.fastf1_loader import FastF1LoaderConfigurationError
from app.ingestion.fastf1_schedule import (
    FastF1ScheduleLoader,
    FastF1ScheduleLoadError,
    FastF1ScheduleNormalizationError,
    create_fastf1_schedule_loader,
    curated_round_numbers_by_event_name,
    normalize_fastf1_schedule,
)


@pytest.fixture(autouse=True)
def reset_active_cache_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastf1_loader, "_active_cache_path", None)
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "EventName": "Bahrain Grand Prix",
                    "RoundNumber": 1,
                }
            ]
        ),
    )


def session(
    name: str,
    *,
    key: int = 1,
    start: str = "2024-03-01T15:00:00",
    end: str = "2024-03-01T16:00:00",
    offset: str = "03:00:00",
) -> dict[str, object]:
    return {
        "Key": key,
        "Name": name,
        "StartDate": start,
        "EndDate": end,
        "GmtOffset": offset,
    }


def meeting(
    round_number: int,
    *,
    name: str = "Bahrain Grand Prix",
    sessions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "Number": round_number,
        "Name": name,
        "OfficialName": f"FORMULA 1 {name.upper()}",
        "Country": {"Name": "Bahrain"},
        "Location": "Sakhir",
        "Sessions": sessions
        or [
            session("Practice 1"),
            session(
                "Qualifying",
                key=2,
                start="2024-03-01T19:00:00",
                end="2024-03-01T20:00:00",
            ),
            session(
                "Race",
                key=3,
                start="2024-03-02T18:00:00",
                end="2024-03-02T20:00:00",
            ),
        ],
    }


def test_normalizes_real_session_boundaries_and_championship_scope() -> None:
    schedule = normalize_fastf1_schedule(
        season_year=2024,
        meetings=[
            meeting(2, name="Saudi Arabian Grand Prix"),
            meeting(0, name="Pre-Season Test"),
            meeting(1),
        ],
    )

    assert schedule.season_year == 2024
    assert [event.round_number for event in schedule.events] == [1, 2]
    event = schedule.events[0]
    assert event.country == "Bahrain"
    assert event.location == "Sakhir"
    assert event.event_format == "conventional"
    assert [item.session_key for item in event.sessions] == [
        "practice_1",
        "qualifying",
        "race",
    ]
    assert event.sessions[0].scheduled_start_at.isoformat() == (
        "2024-03-01T12:00:00+00:00"
    )
    assert event.sessions[0].scheduled_end_at.isoformat() == (
        "2024-03-01T13:00:00+00:00"
    )
    assert event.starts_at == event.sessions[0].scheduled_start_at
    assert event.ends_at == event.sessions[-1].scheduled_end_at


def test_normalizes_sprint_names_and_future_session_keys() -> None:
    schedule_2021 = normalize_fastf1_schedule(
        season_year=2021,
        meetings=[
            meeting(
                1,
                sessions=[
                    session("Practice 1"),
                    session("Sprint Qualifying", key=2),
                ],
            )
        ],
    )
    schedule_future = normalize_fastf1_schedule(
        season_year=2026,
        meetings=[
            meeting(
                1,
                sessions=[session("Rookie Practice", key=1)],
            )
        ],
    )

    assert schedule_2021.events[0].sessions[1].session_name == "Sprint"
    assert schedule_2021.events[0].sessions[1].session_key == "sprint"
    assert schedule_2021.events[0].event_format == "sprint"
    assert (
        schedule_future.events[0].sessions[0].session_key
        == "rookie_practice"
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data[0]["Sessions"][0].pop("EndDate"),
            "invalid EndDate",
        ),
        (
            lambda data: data.append(meeting(1)),
            "duplicate championship round",
        ),
        (
            lambda data: data[0]["Sessions"].append(
                session("Race", key=4)
            ),
            "duplicate session key",
        ),
        (
            lambda data: data[0]["Sessions"][0].update(
                {"EndDate": "2024-03-01T14:00:00"}
            ),
            "must end after",
        ),
    ],
)
def test_rejects_incomplete_or_ambiguous_snapshots(
    mutate: Any,
    message: str,
) -> None:
    meetings = [meeting(1)]
    mutate(meetings)

    with pytest.raises(
        FastF1ScheduleNormalizationError,
        match=message,
    ):
        normalize_fastf1_schedule(
            season_year=2024,
            meetings=meetings,
        )


def test_rejects_empty_schedule_and_unsupported_year() -> None:
    with pytest.raises(
        FastF1ScheduleNormalizationError,
        match="no championship events",
    ):
        normalize_fastf1_schedule(
            season_year=2024,
            meetings=[meeting(0, name="Pre-Season Test")],
        )

    with pytest.raises(FastF1LoaderConfigurationError, match="2018"):
        normalize_fastf1_schedule(season_year=2017, meetings=[])


def test_loader_uses_persistent_cache_and_pinned_season_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_calls: list[tuple[str, dict[str, bool]]] = []
    schedule_calls: list[str] = []

    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda cache_path, **options: cache_calls.append(
            (cache_path, options)
        ),
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda path: schedule_calls.append(path) or [meeting(1)],
    )

    loaded = FastF1ScheduleLoader(tmp_path / "cache").load(2024)

    assert loaded.season_year == 2024
    assert schedule_calls == ["/static/2024/"]
    assert cache_calls == [
        (
            str(tmp_path / "cache"),
            {
                "ignore_version": False,
                "force_renew": False,
                "use_requests_cache": True,
            },
        )
    ]


def test_loader_wraps_upstream_failure_without_partial_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )

    def fail(_path: str) -> None:
        raise ConnectionError("controlled upstream failure")

    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        fail,
    )

    with pytest.raises(FastF1ScheduleLoadError, match="2024") as error:
        FastF1ScheduleLoader(tmp_path / "cache").load(2024)

    assert isinstance(error.value.__cause__, ConnectionError)


def test_loader_reconciles_duplicate_private_rounds_by_curated_event_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    private_meetings = [
        meeting(6, name="Miami Grand Prix"),
        meeting(6, name="Monaco Grand Prix"),
    ]
    public_schedule = pd.DataFrame(
        [
            {"EventName": "Miami Grand Prix", "RoundNumber": 6},
            {"EventName": "Monaco Grand Prix", "RoundNumber": 8},
        ]
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: private_meetings,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: public_schedule,
    )

    loaded = FastF1ScheduleLoader(tmp_path / "cache").load(2026)

    assert [
        (event.round_number, event.event_name)
        for event in loaded.events
    ] == [
        (6, "Miami Grand Prix"),
        (8, "Monaco Grand Prix"),
    ]
    assert loaded.events[1].sessions[0].scheduled_end_at.isoformat() == (
        "2024-03-01T13:00:00+00:00"
    )


def test_loader_hydrates_event_missing_from_private_season_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: [meeting(2)],
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "RoundNumber": 1,
                    "Country": "Australia",
                    "Location": "Melbourne",
                    "OfficialEventName": (
                        "FORMULA 1 2018 ROLEX AUSTRALIAN GRAND PRIX"
                    ),
                    "EventName": "Australian Grand Prix",
                    "Session1": "Practice 1",
                    "Session2": "Practice 2",
                    "Session3": "Practice 3",
                    "Session4": "Qualifying",
                    "Session5": "Race",
                },
                {
                    "RoundNumber": 2,
                    "EventName": "Bahrain Grand Prix",
                },
            ]
        ),
    )

    session_requests: list[tuple[int, int, str, str]] = []
    metadata_paths: list[str] = []

    def get_session(
        year: int,
        round_number: int,
        session_name: str,
        *,
        backend: str,
    ) -> SimpleNamespace:
        session_requests.append(
            (year, round_number, session_name, backend)
        )
        path_name = session_name.replace(" ", "_")
        return SimpleNamespace(
            api_path=f"/static/2018/australia/{path_name}/"
        )

    def session_info(path: str) -> dict[str, object]:
        metadata_paths.append(path)
        session_name = path.rstrip("/").rsplit("/", 1)[-1].replace(
            "_",
            " ",
        )
        starts = {
            "Practice 1": "2018-03-23T12:00:00",
            "Practice 2": "2018-03-23T16:00:00",
            "Practice 3": "2018-03-24T14:00:00",
            "Qualifying": "2018-03-24T17:00:00",
            "Race": "2018-03-25T16:10:00",
        }
        ends = {
            "Practice 1": "2018-03-23T13:30:00",
            "Practice 2": "2018-03-23T17:30:00",
            "Practice 3": "2018-03-24T15:00:00",
            "Qualifying": "2018-03-24T18:00:00",
            "Race": "2018-03-25T18:10:00",
        }
        return {
            "Key": 1,
            "StartDate": starts[session_name],
            "EndDate": ends[session_name],
            "GmtOffset": "11:00:00",
        }

    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_session",
        get_session,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "session_info",
        session_info,
    )

    loaded = FastF1ScheduleLoader(tmp_path / "cache").load(2018)

    assert [
        (event.round_number, event.event_name)
        for event in loaded.events
    ] == [
        (1, "Australian Grand Prix"),
        (2, "Bahrain Grand Prix"),
    ]
    australian = loaded.events[0]
    assert australian.country == "Australia"
    assert australian.location == "Melbourne"
    assert len(australian.sessions) == 5
    assert australian.sessions[0].scheduled_start_at.isoformat() == (
        "2018-03-23T01:00:00+00:00"
    )
    assert australian.sessions[-1].scheduled_end_at.isoformat() == (
        "2018-03-25T07:10:00+00:00"
    )
    assert [request[2] for request in session_requests] == [
        "Practice 1",
        "Practice 2",
        "Practice 3",
        "Qualifying",
        "Race",
    ]
    assert len(metadata_paths) == 5


def test_loader_defers_unpublished_future_events_in_current_season(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: [meeting(1)],
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "RoundNumber": 1,
                    "EventName": "Bahrain Grand Prix",
                },
                {
                    "RoundNumber": 12,
                    "EventName": "Dutch Grand Prix",
                    "Session1": "Practice 1",
                    "Session1DateUtc": pd.Timestamp(
                        "2026-08-21 10:30:00"
                    ),
                    "Session2": "Sprint Qualifying",
                    "Session2DateUtc": pd.Timestamp(
                        "2026-08-21 14:30:00"
                    ),
                    "Session3": "Sprint",
                    "Session3DateUtc": pd.Timestamp(
                        "2026-08-22 10:00:00"
                    ),
                    "Session4": "Qualifying",
                    "Session4DateUtc": pd.Timestamp(
                        "2026-08-22 14:00:00"
                    ),
                    "Session5": "Race",
                    "Session5DateUtc": pd.Timestamp(
                        "2026-08-23 13:00:00"
                    ),
                },
            ]
        ),
    )

    def unexpected_session_load(*_args, **_kwargs):
        raise AssertionError("future event metadata must be deferred")

    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_session",
        unexpected_session_load,
    )

    loaded = FastF1ScheduleLoader(
        tmp_path / "cache",
        now_provider=lambda: datetime(2026, 7, 28, 12, tzinfo=UTC),
    ).load(2026)

    assert [event.event_name for event in loaded.events] == [
        "Bahrain Grand Prix"
    ]
    assert loaded.deferred_future_events == (
        fastf1_schedule.DeferredFutureEvent(
            round_number=12,
            event_name="Dutch Grand Prix",
            scheduled_start_at=datetime(
                2026,
                8,
                21,
                10,
                30,
                tzinfo=UTC,
            ),
        ),
    )


def test_loader_keeps_current_season_started_missing_event_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: [meeting(1)],
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "RoundNumber": 1,
                    "EventName": "Bahrain Grand Prix",
                },
                {
                    "RoundNumber": 2,
                    "EventName": "Started Grand Prix",
                    "Session1": "Practice 1",
                    "Session1DateUtc": pd.Timestamp(
                        "2026-07-27 10:00:00"
                    ),
                },
            ]
        ),
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConnectionError("controlled unavailable metadata")
        ),
    )

    with pytest.raises(
        FastF1ScheduleLoadError,
        match="Started Grand Prix",
    ):
        FastF1ScheduleLoader(
            tmp_path / "cache",
            now_provider=lambda: datetime(
                2026,
                7,
                28,
                12,
                tzinfo=UTC,
            ),
        ).load(2026)


def test_curated_round_authority_rejects_ambiguous_events() -> None:
    with pytest.raises(
        FastF1ScheduleNormalizationError,
        match="duplicate curated event name",
    ):
        curated_round_numbers_by_event_name(
            pd.DataFrame(
                [
                    {
                        "EventName": "Monaco Grand Prix",
                        "RoundNumber": 7,
                    },
                    {
                        "EventName": " monaco   grand prix ",
                        "RoundNumber": 8,
                    },
                ]
            )
        )


def test_loader_rejects_duplicate_round_when_curated_mapping_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: [
            meeting(6, name="Miami Grand Prix"),
            meeting(6, name="Monaco Grand Prix"),
        ],
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [{"EventName": "Miami Grand Prix", "RoundNumber": 6}]
        ),
    )

    with pytest.raises(
        FastF1ScheduleNormalizationError,
        match="absent from the curated schedule",
    ):
        FastF1ScheduleLoader(tmp_path / "cache").load(2026)


def test_schedule_loads_share_the_fastf1_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_lock = Lock()
    active_loads = 0
    maximum_active_loads = 0

    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )

    def load_schedule(_path: str) -> list[dict[str, object]]:
        nonlocal active_loads, maximum_active_loads
        with state_lock:
            active_loads += 1
            maximum_active_loads = max(maximum_active_loads, active_loads)
        time.sleep(0.02)
        with state_lock:
            active_loads -= 1
        return [meeting(1)]

    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        load_schedule,
    )
    loader = FastF1ScheduleLoader(tmp_path / "cache")

    with ThreadPoolExecutor(max_workers=2) as executor:
        schedules = list(executor.map(loader.load, (2024, 2025)))

    assert len(schedules) == 2
    assert maximum_active_loads == 1


def test_factory_uses_environment_cache_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "environment-cache"
    monkeypatch.setenv("FASTF1_CACHE_PATH", str(cache_path))

    loader = create_fastf1_schedule_loader()

    assert loader.cache_path == cache_path


def test_loader_defers_an_event_upstream_has_not_published_yet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race weekend in progress must not fail the whole season.

    Once an event's first session has started it is no longer future-dated, so
    the loader tries to fetch its exact session metadata. Upstream publishes
    that only after the fact, and the gap between the two made every 2026
    backfill answer 503 while the Dutch Grand Prix was running.
    """
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        lambda _path: [meeting(1, name="Bahrain Grand Prix")],
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {"RoundNumber": 1, "EventName": "Bahrain Grand Prix"},
                {
                    "RoundNumber": 2,
                    "EventName": "Dutch Grand Prix",
                    "Session1": "Practice 1",
                    "Session1DateUtc": pd.Timestamp("2024-03-08T10:30:00"),
                    "Session2": "Qualifying",
                    "Session2DateUtc": pd.Timestamp("2024-03-09T14:00:00"),
                },
            ]
        ),
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_session",
        lambda *_args, **_kwargs: SimpleNamespace(
            api_path="/static/2024/zandvoort/Practice_1/"
        ),
    )

    def session_info(_path: str) -> dict[str, object]:
        raise fastf1_schedule.SessionNotAvailableError(
            "No data for this session!"
        )

    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "session_info",
        session_info,
    )

    # The weekend has already begun, so the event is not future-dated and
    # cannot be deferred by the ordinary rule.
    loader = FastF1ScheduleLoader(
        tmp_path / "cache",
        now_provider=lambda: datetime(2024, 3, 8, 12, 0, tzinfo=UTC),
    )
    loaded = loader.load(2024)

    assert [event.event_name for event in loaded.events] == [
        "Bahrain Grand Prix",
    ]
    assert [
        (event.round_number, event.event_name)
        for event in loaded.deferred_future_events
    ] == [(2, "Dutch Grand Prix")]


def test_loader_reads_the_season_index_without_fastf1_caching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index grows during a season, and FastF1 caches it forever.

    ``Cache._data_ok_for_use`` checks a version number and never an age, so a
    season index parsed in July is reused for the rest of the year. Every event
    confirmed after that point stays invisible, which is what hid the Dutch
    Grand Prix from this archive while it was already running.
    """
    monkeypatch.setattr(
        fastf1_loader.fastf1.Cache,
        "enable_cache",
        lambda *_args, **_kwargs: None,
    )

    disabled_during: list[bool] = []
    monkeypatch.setattr(
        fastf1_schedule.fastf1.Cache,
        "set_disabled",
        lambda: setattr(fastf1_schedule.fastf1.Cache, "_tmp_disabled", True),
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1.Cache,
        "set_enabled",
        lambda: setattr(fastf1_schedule.fastf1.Cache, "_tmp_disabled", False),
    )

    def season_schedule(_path: str) -> list[dict[str, object]]:
        disabled_during.append(
            bool(getattr(fastf1_schedule.fastf1.Cache, "_tmp_disabled", False))
        )
        return [meeting(1)]

    monkeypatch.setattr(
        fastf1_schedule.fastf1._api,
        "season_schedule",
        season_schedule,
    )
    monkeypatch.setattr(
        fastf1_schedule.fastf1,
        "get_event_schedule",
        lambda *_args, **_kwargs: pd.DataFrame(
            [{"RoundNumber": 1, "EventName": "Bahrain Grand Prix"}]
        ),
    )

    FastF1ScheduleLoader(tmp_path / "cache").load(2024)

    assert disabled_during == [True], "the index was served from a cache"
    # Caching is restored for everything else, which does not change once
    # published and is expensive to refetch.
    assert getattr(fastf1_schedule.fastf1.Cache, "_tmp_disabled", False) is False
