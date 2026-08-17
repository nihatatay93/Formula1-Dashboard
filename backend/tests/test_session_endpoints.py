from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.contracts import (
    DataSource,
    LapSummary,
    LapSummaryFilters,
    LapSummaryPage,
    LapSummaryQuery,
    LapSummaryResponse,
    RacePaceEntry,
    RacePaceFilters,
    RacePaceLap,
    RacePaceQuery,
    RacePaceResponse,
    RecordState,
    SessionDetailCounts,
    SessionDetailEvent,
    SessionDetailResponse,
    SessionEntryResult,
    SessionResultData,
    SessionResultsResponse,
    SessionSnapshot,
)
from app.api.dependencies import get_database_session_factory
from app.api.session_data import (
    SessionDataUnavailableError,
    SessionEntryNotFoundError,
    SessionNotFoundError,
)
from app.main import app

SESSION_ID = 210
SESSION_ENTRY_ID = 1001
COMPLETED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _snapshot() -> SessionSnapshot:
    return SessionSnapshot(
        data_available=True,
        source=DataSource.FASTF1_ARCHIVE,
        record_state=RecordState.FINALIZED,
        completed_at=COMPLETED_AT,
        source_updated_at=COMPLETED_AT,
    )


def _detail_response() -> SessionDetailResponse:
    return SessionDetailResponse(
        id=SESSION_ID,
        session_key="race",
        session_name="Race",
        scheduled_start_at=datetime(2024, 3, 2, 15, tzinfo=UTC),
        scheduled_end_at=datetime(2024, 3, 2, 17, tzinfo=UTC),
        event=SessionDetailEvent(
            id=42,
            season_year=2024,
            round_number=1,
            official_name="FORMULA 1 BAHRAIN GRAND PRIX 2024",
            event_name="Bahrain Grand Prix",
            country="Bahrain",
            location="Sakhir",
            event_format="conventional",
        ),
        snapshot=_snapshot(),
        ingestion=None,
        counts=SessionDetailCounts(entries=20, results=20, laps=1124),
    )


def _results_response() -> SessionResultsResponse:
    return SessionResultsResponse(
        session_id=SESSION_ID,
        snapshot=_snapshot(),
        items=(
            SessionEntryResult(
                session_entry_id=SESSION_ENTRY_ID,
                driver=None,
                racing_number="1",
                abbreviation="EXD",
                broadcast_name="E DRIVER",
                display_name="Example Driver",
                team_jolpica_id="example_team",
                team_name="Example Team",
                team_color_hex="#3671C6",
                source=DataSource.FASTF1_ARCHIVE,
                record_state=RecordState.FINALIZED,
                result=SessionResultData(
                    position=1,
                    classified_position="1",
                    grid_position=1,
                    points=Decimal("26.000"),
                    status="Finished",
                    laps_completed=57,
                    q1_time_us=None,
                    q2_time_us=None,
                    q3_time_us=None,
                    elapsed_time_us=5_504_742_000,
                    gap_to_leader_us=0,
                    gap_to_leader_laps=0,
                    source=DataSource.FASTF1_ARCHIVE,
                    record_state=RecordState.FINALIZED,
                ),
            ),
        ),
    )


def _laps_response(query: LapSummaryQuery) -> LapSummaryResponse:
    return LapSummaryResponse(
        session_id=SESSION_ID,
        session_entry_id=SESSION_ENTRY_ID,
        snapshot=_snapshot(),
        filters=LapSummaryFilters(
            lap_from=query.lap_from,
            lap_to=query.lap_to,
            stint_number=query.stint_number,
            include_deleted=query.include_deleted,
        ),
        page=LapSummaryPage(
            limit=query.limit,
            has_more=False,
            next_after_lap=None,
        ),
        items=(
            LapSummary(
                id=9001,
                lap_number=12,
                stint_number=2,
                session_time_us=96_345_123,
                lap_time_us=95_543_210,
                lap_start_time_us=802_000,
                pit_out_time_us=None,
                pit_in_time_us=None,
                sector_1_time_us=31_000_123,
                sector_2_time_us=42_000_456,
                sector_3_time_us=22_542_631,
                sector_1_session_time_us=31_000_123,
                sector_2_session_time_us=73_000_579,
                sector_3_session_time_us=95_543_210,
                speed_i1_kph=284.1,
                speed_i2_kph=301.8,
                speed_fl_kph=276.4,
                speed_st_kph=319.2,
                is_personal_best=False,
                compound="MEDIUM",
                tyre_life_laps=4,
                fresh_tyre=False,
                track_status="1",
                position=1,
                deleted=False,
                deleted_reason=None,
                fastf1_generated=False,
                is_accurate=True,
                source=DataSource.FASTF1_ARCHIVE,
                record_state=RecordState.FINALIZED,
            ),
        ),
    )


def test_session_detail_endpoint_returns_contract_and_disables_caching(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    calls: list[tuple[int, object]] = []
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_session_detail(*, session_id, session_factory):
        calls.append((session_id, session_factory))
        return _detail_response()

    monkeypatch.setattr(
        "app.api.sessions.read_session_detail",
        stub_read_session_detail,
    )

    response = client.get(f"/api/v1/sessions/{SESSION_ID}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["id"] == str(SESSION_ID)
    assert response.json()["event"]["id"] == "42"
    assert response.json()["counts"] == {
        "entries": 20,
        "results": 20,
        "laps": 1124,
    }
    assert calls == [(SESSION_ID, sentinel_factory)]


def test_session_results_endpoint_returns_exact_values_and_disables_caching(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_session_results(*, session_id, session_factory):
        assert (session_id, session_factory) == (
            SESSION_ID,
            sentinel_factory,
        )
        return _results_response()

    monkeypatch.setattr(
        "app.api.sessions.read_session_results",
        stub_read_session_results,
    )

    response = client.get(f"/api/v1/sessions/{SESSION_ID}/results")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["session_id"] == str(SESSION_ID)
    assert response.json()["items"][0]["session_entry_id"] == str(
        SESSION_ENTRY_ID
    )
    assert response.json()["items"][0]["result"]["points"] == "26.000"


def test_session_laps_endpoint_maps_every_query_parameter(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    calls: list[tuple[int, int, LapSummaryQuery, object]] = []
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_session_laps(
        *,
        session_id,
        session_entry_id,
        query,
        session_factory,
    ):
        calls.append(
            (session_id, session_entry_id, query, session_factory)
        )
        return _laps_response(query)

    monkeypatch.setattr(
        "app.api.sessions.read_session_laps",
        stub_read_session_laps,
    )

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/entries/{SESSION_ENTRY_ID}/laps",
        params={
            "after_lap": 10,
            "limit": 25,
            "lap_from": 5,
            "lap_to": 20,
            "stint_number": 2,
            "include_deleted": "false",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["filters"] == {
        "lap_from": 5,
        "lap_to": 20,
        "stint_number": 2,
        "include_deleted": False,
    }
    assert response.json()["page"]["limit"] == 25
    assert response.json()["items"][0]["id"] == "9001"
    assert calls == [
        (
            SESSION_ID,
            SESSION_ENTRY_ID,
            LapSummaryQuery(
                after_lap=10,
                limit=25,
                lap_from=5,
                lap_to=20,
                stint_number=2,
                include_deleted=False,
            ),
            sentinel_factory,
        )
    ]


def test_session_laps_endpoint_uses_accepted_query_defaults(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()
    captured_queries: list[LapSummaryQuery] = []

    def stub_read_session_laps(*, query, **_kwargs):
        captured_queries.append(query)
        return _laps_response(query)

    monkeypatch.setattr(
        "app.api.sessions.read_session_laps",
        stub_read_session_laps,
    )

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/entries/{SESSION_ENTRY_ID}/laps"
    )

    assert response.status_code == 200
    assert captured_queries == [LapSummaryQuery()]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/sessions/not-an-integer",
        "/api/v1/sessions/0",
        "/api/v1/sessions/0/results",
        "/api/v1/sessions/210/entries/not-an-integer/laps",
        "/api/v1/sessions/210/entries/0/laps",
    ],
)

def test_session_endpoints_keep_fastapi_path_validation(
    client: TestClient,
    monkeypatch,
    path: str,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def forbidden_read(**_kwargs):
        raise AssertionError("read service must not be called")

    monkeypatch.setattr(
        "app.api.sessions.read_session_detail",
        forbidden_read,
    )
    monkeypatch.setattr(
        "app.api.sessions.read_session_results",
        forbidden_read,
    )
    monkeypatch.setattr(
        "app.api.sessions.read_session_laps",
        forbidden_read,
    )

    response = client.get(path)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize(
    "params",
    [
        {"after_lap": -1},
        {"limit": 0},
        {"limit": 101},
        {"lap_from": 0},
        {"lap_to": 0},
        {"stint_number": 0},
        {"include_deleted": "not-a-boolean"},
    ],
)
def test_session_laps_keeps_fastapi_query_validation(
    client: TestClient,
    monkeypatch,
    params: dict[str, object],
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def forbidden_read(**_kwargs):
        raise AssertionError("read service must not be called")

    monkeypatch.setattr(
        "app.api.sessions.read_session_laps",
        forbidden_read,
    )

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/entries/{SESSION_ENTRY_ID}/laps",
        params=params,
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_session_laps_returns_stable_invalid_range(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def forbidden_read(**_kwargs):
        raise AssertionError("read service must not be called")

    monkeypatch.setattr(
        "app.api.sessions.read_session_laps",
        forbidden_read,
    )

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/entries/{SESSION_ENTRY_ID}/laps",
        params={"lap_from": 20, "lap_to": 10},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "invalid_lap_range",
            "message": "The requested lap range is invalid.",
        }
    }


@pytest.mark.parametrize(
    ("path", "service", "error_type", "status_code", "code", "message"),
    [
        (
            f"/api/v1/sessions/{SESSION_ID}",
            "read_session_detail",
            SessionNotFoundError,
            404,
            "session_not_found",
            "Historical session was not found.",
        ),
        (
            f"/api/v1/sessions/{SESSION_ID}/results",
            "read_session_results",
            SessionNotFoundError,
            404,
            "session_not_found",
            "Historical session was not found.",
        ),
        (
            (
                f"/api/v1/sessions/{SESSION_ID}/entries/"
                f"{SESSION_ENTRY_ID}/laps"
            ),
            "read_session_laps",
            SessionNotFoundError,
            404,
            "session_not_found",
            "Historical session was not found.",
        ),
        (
            f"/api/v1/sessions/{SESSION_ID}/results",
            "read_session_results",
            SessionDataUnavailableError,
            409,
            "session_data_unavailable",
            "Historical data is not available for this session.",
        ),
        (
            (
                f"/api/v1/sessions/{SESSION_ID}/entries/"
                f"{SESSION_ENTRY_ID}/laps"
            ),
            "read_session_laps",
            SessionEntryNotFoundError,
            404,
            "session_entry_not_found",
            "Session entry was not found.",
        ),
        (
            (
                f"/api/v1/sessions/{SESSION_ID}/entries/"
                f"{SESSION_ENTRY_ID}/laps"
            ),
            "read_session_laps",
            SessionDataUnavailableError,
            409,
            "session_data_unavailable",
            "Historical data is not available for this session.",
        ),
    ],
)
def test_session_endpoints_map_domain_failures_without_details(
    client: TestClient,
    monkeypatch,
    path: str,
    service: str,
    error_type: type[Exception],
    status_code: int,
    code: str,
    message: str,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def fail_read(**_kwargs):
        raise error_type("RAW-DOMAIN-ERROR-SENTINEL")

    monkeypatch.setattr(f"app.api.sessions.{service}", fail_read)

    response = client.get(path)

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {"code": code, "message": message}
    }
    assert "RAW-DOMAIN-ERROR-SENTINEL" not in response.text


@pytest.mark.parametrize(
    ("path", "service"),
    [
        (
            f"/api/v1/sessions/{SESSION_ID}",
            "read_session_detail",
        ),
        (
            f"/api/v1/sessions/{SESSION_ID}/results",
            "read_session_results",
        ),
        (
            (
                f"/api/v1/sessions/{SESSION_ID}/entries/"
                f"{SESSION_ENTRY_ID}/laps"
            ),
            "read_session_laps",
        ),
    ],
)
def test_session_endpoints_map_database_failures_without_details(
    client: TestClient,
    monkeypatch,
    path: str,
    service: str,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def fail_read(**_kwargs):
        raise SQLAlchemyError("RAW-DATABASE-ERROR-SENTINEL")

    monkeypatch.setattr(f"app.api.sessions.{service}", fail_read)

    response = client.get(path)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "The database is temporarily unavailable.",
        }
    }
    assert "RAW-DATABASE-ERROR-SENTINEL" not in response.text


def test_openapi_documents_historical_session_routes() -> None:
    paths = app.openapi()["paths"]
    detail = paths["/api/v1/sessions/{session_id}"]["get"]
    results = paths["/api/v1/sessions/{session_id}/results"]["get"]
    laps = paths[
        "/api/v1/sessions/{session_id}/entries/{session_entry_id}/laps"
    ]["get"]
    race_pace = paths["/api/v1/sessions/{session_id}/laps"]["get"]

    assert detail["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/SessionDetailResponse"}
    assert results["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/SessionResultsResponse"}
    assert laps["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/LapSummaryResponse"}
    assert race_pace["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/RacePaceResponse"}

    for operation in (detail, results, laps, race_pace):
        for status_code in ("500", "503"):
            assert operation["responses"][status_code]["content"][
                "application/json"
            ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}

    parameter_schemas = {
        parameter["name"]: parameter["schema"]
        for parameter in laps["parameters"]
    }
    assert parameter_schemas["session_id"]["minimum"] == 1
    assert parameter_schemas["session_entry_id"]["minimum"] == 1
    assert parameter_schemas["after_lap"]["anyOf"][0]["minimum"] == 0

    race_pace_schemas = {
        parameter["name"]: parameter["schema"]
        for parameter in race_pace["parameters"]
    }
    assert race_pace_schemas["outlier_cutoff"]["minimum"] == 100.0
    assert race_pace_schemas["outlier_cutoff"]["maximum"] == 200.0
    assert parameter_schemas["limit"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "default": 50,
        "title": "Limit",
    }
    assert parameter_schemas["include_deleted"]["default"] is True
    assert laps["responses"]["422"]["content"]["application/json"][
        "schema"
    ]["oneOf"] == [
        {"$ref": "#/components/schemas/HTTPValidationError"},
        {"$ref": "#/components/schemas/ErrorResponse"},
    ]


def _race_pace_response(query: RacePaceQuery) -> RacePaceResponse:
    return RacePaceResponse(
        session_id=SESSION_ID,
        snapshot=_snapshot(),
        filters=RacePaceFilters(
            clean_only=query.clean_only,
            outlier_cutoff=query.outlier_cutoff,
        ),
        clean_lap_definition="A lap is clean when ...",
        session_best_lap_time_us=90_000_000,
        outlier_cutoff_lap_time_us=96_300_000,
        items=(
            RacePaceEntry(
                session_entry_id=SESSION_ENTRY_ID,
                driver_id=77,
                display_name="Ada Leader",
                abbreviation="ADA",
                racing_number="1",
                team_name="Example Team",
                team_color_hex="#27F4D2",
                finishing_position=1,
                laps=(
                    RacePaceLap(
                        lap_number=2,
                        lap_time_us=90_000_000,
                        stint_number=1,
                        pit_in_time_us=None,
                        pit_out_time_us=None,
                        track_status="1",
                        compound="MEDIUM",
                        tyre_life_laps=2,
                        position=1,
                        is_clean=True,
                        is_personal_best=True,
                        beyond_cutoff=False,
                    ),
                ),
            ),
        ),
    )


def test_race_pace_endpoint_maps_every_query_parameter(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    calls: list[tuple[int, RacePaceQuery, object]] = []
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_race_pace(*, session_id, query, session_factory):
        calls.append((session_id, query, session_factory))
        return _race_pace_response(query)

    monkeypatch.setattr("app.api.sessions.read_race_pace", stub_read_race_pace)

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/laps",
        params={"clean_only": "true", "outlier_cutoff": 110},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["filters"] == {
        "clean_only": True,
        "outlier_cutoff": 110.0,
    }
    assert calls == [
        (
            SESSION_ID,
            RacePaceQuery(clean_only=True, outlier_cutoff=110.0),
            sentinel_factory,
        )
    ]


def test_race_pace_endpoint_uses_accepted_query_defaults(
    client: TestClient,
    monkeypatch,
) -> None:
    captured: list[RacePaceQuery] = []
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def stub_read_race_pace(*, session_id, query, session_factory):
        captured.append(query)
        return _race_pace_response(query)

    monkeypatch.setattr("app.api.sessions.read_race_pace", stub_read_race_pace)

    response = client.get(f"/api/v1/sessions/{SESSION_ID}/laps")

    assert response.status_code == 200
    # Every lap by default: a chart that silently dropped the pit laps would
    # be lying about the race.
    assert captured == [RacePaceQuery(clean_only=False, outlier_cutoff=107.0)]


def test_race_pace_rejects_a_cutoff_below_the_session_best(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    response = client.get(
        f"/api/v1/sessions/{SESSION_ID}/laps",
        params={"outlier_cutoff": 99},
    )

    # No lap can beat the best by definition, so a cutoff under 100% would
    # mark the entire session as outlying.
    assert response.status_code == 422
