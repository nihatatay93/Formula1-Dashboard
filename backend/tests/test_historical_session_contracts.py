from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.contracts import (
    DataSource,
    LapSummary,
    LapSummaryFilters,
    LapSummaryPage,
    LapSummaryQuery,
    LapSummaryResponse,
    NonnegativeDecimalString,
    RecordState,
    SessionDetailCounts,
    SessionDetailEvent,
    SessionDetailResponse,
    SessionEntryResult,
    SessionResultData,
    SessionResultDriver,
    SessionResultsResponse,
    SessionSnapshot,
)

_COMPLETED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _available_snapshot() -> SessionSnapshot:
    return SessionSnapshot(
        data_available=True,
        source=DataSource.FASTF1_ARCHIVE,
        record_state=RecordState.FINALIZED,
        completed_at=_COMPLETED_AT,
        source_updated_at=datetime(
            2026,
            7,
            28,
            14,
            59,
            58,
            tzinfo=timezone(timedelta(hours=3)),
        ),
    )


def _result_item(
    *,
    session_entry_id: int,
    position: int | None,
    points: Decimal | None = None,
    team_color_hex: str | None = "#3671C6",
) -> SessionEntryResult:
    return SessionEntryResult(
        session_entry_id=session_entry_id,
        driver=SessionResultDriver(
            id=session_entry_id + 100,
            jolpica_driver_id="driver_id",
            given_name="Example",
            family_name="Driver",
            full_name="Example Driver",
            country_code="GBR",
        ),
        racing_number="1",
        abbreviation="EXD",
        broadcast_name="E DRIVER",
        display_name="Example Driver",
        team_jolpica_id="example_team",
        team_name="Example Team",
        team_color_hex=team_color_hex,
        source=DataSource.FASTF1_ARCHIVE,
        record_state=RecordState.FINALIZED,
        result=SessionResultData(
            position=position,
            classified_position=str(position) if position is not None else None,
            grid_position=1,
            points=points,
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
    )


def _lap(
    *,
    lap_id: int,
    lap_number: int,
    stint_number: int | None = 1,
    deleted: bool | None = False,
) -> LapSummary:
    return LapSummary(
        id=lap_id,
        lap_number=lap_number,
        stint_number=stint_number,
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
        compound="SOFT",
        tyre_life_laps=lap_number,
        fresh_tyre=True,
        track_status="1",
        position=1,
        deleted=deleted,
        deleted_reason=None,
        fastf1_generated=False,
        is_accurate=True,
        source=DataSource.FASTF1_ARCHIVE,
        record_state=RecordState.FINALIZED,
    )


def _lap_response(
    *,
    items: tuple[LapSummary, ...],
    filters: LapSummaryFilters | None = None,
    page: LapSummaryPage | None = None,
    snapshot: SessionSnapshot | None = None,
) -> LapSummaryResponse:
    return LapSummaryResponse(
        session_id=210,
        session_entry_id=1001,
        snapshot=snapshot or _available_snapshot(),
        filters=filters
        or LapSummaryFilters(
            lap_from=None,
            lap_to=None,
            stint_number=None,
            include_deleted=True,
        ),
        page=page
        or LapSummaryPage(
            limit=50,
            has_more=False,
            next_after_lap=None,
        ),
        items=items,
    )


def test_exact_decimal_values_serialize_as_strings_without_float_input() -> None:
    adapter = TypeAdapter(NonnegativeDecimalString)

    assert adapter.validate_python(Decimal("26.000")) == "26.000"
    assert adapter.validate_python("0.500") == "0.500"
    assert adapter.dump_python(
        adapter.validate_python(26),
        mode="json",
    ) == "26"

    for invalid in (-1, Decimal("NaN"), Decimal("Infinity"), 0.5, True):
        with pytest.raises(
            ValidationError,
            match="exact non-negative number",
        ):
            adapter.validate_python(invalid)


def test_snapshot_requires_consistent_availability_metadata() -> None:
    unavailable = SessionSnapshot(
        data_available=False,
        source=None,
        record_state=None,
        completed_at=None,
        source_updated_at=None,
    )

    assert unavailable.data_available is False

    with pytest.raises(
        ValidationError,
        match="available snapshot requires",
    ):
        SessionSnapshot(
            data_available=True,
            source=None,
            record_state=RecordState.FINALIZED,
            completed_at=_COMPLETED_AT,
            source_updated_at=None,
        )

    with pytest.raises(
        ValidationError,
        match="unavailable snapshot cannot expose",
    ):
        SessionSnapshot(
            data_available=False,
            source=None,
            record_state=None,
            completed_at=None,
            source_updated_at=_COMPLETED_AT,
        )


def test_session_detail_serializes_ids_and_timestamps_for_clients() -> None:
    response = SessionDetailResponse(
        id=210,
        session_key="race",
        session_name="Race",
        scheduled_start_at=datetime(
            2024,
            3,
            2,
            18,
            tzinfo=timezone(timedelta(hours=3)),
        ),
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
        snapshot=_available_snapshot(),
        ingestion=None,
        counts=SessionDetailCounts(entries=20, results=20, laps=1124),
    )

    payload = response.model_dump(mode="json")

    assert payload["id"] == "210"
    assert payload["event"]["id"] == "42"
    assert payload["scheduled_start_at"] == "2024-03-02T15:00:00Z"
    assert payload["snapshot"]["source_updated_at"] == "2026-07-28T11:59:58Z"


def test_unavailable_session_detail_requires_zero_sporting_counts() -> None:
    with pytest.raises(
        ValidationError,
        match="unavailable session snapshot must have zero counts",
    ):
        SessionDetailResponse(
            id=210,
            session_key="race",
            session_name="Race",
            scheduled_start_at=None,
            scheduled_end_at=None,
            event=SessionDetailEvent(
                id=42,
                season_year=2024,
                round_number=1,
                official_name=None,
                event_name="Bahrain Grand Prix",
                country=None,
                location=None,
                event_format=None,
            ),
            snapshot=SessionSnapshot(
                data_available=False,
                source=None,
                record_state=None,
                completed_at=None,
                source_updated_at=None,
            ),
            ingestion=None,
            counts=SessionDetailCounts(entries=1, results=0, laps=0),
        )


def test_result_response_preserves_exact_points_and_nullable_identity() -> None:
    resolved = _result_item(
        session_entry_id=1001,
        position=1,
        points=Decimal("26.000"),
    )
    unresolved = SessionEntryResult(
        session_entry_id=1002,
        driver=None,
        racing_number="45",
        abbreviation=None,
        broadcast_name=None,
        display_name="Unresolved Driver",
        team_jolpica_id=None,
        team_name=None,
        team_color_hex=None,
        source=DataSource.FASTF1_ARCHIVE,
        record_state=RecordState.FINALIZED,
        result=None,
    )
    response = SessionResultsResponse(
        session_id=210,
        snapshot=_available_snapshot(),
        items=(resolved, unresolved),
    )

    payload = response.model_dump(mode="json")

    assert payload["session_id"] == "210"
    assert payload["items"][0]["result"]["points"] == "26.000"
    assert payload["items"][1]["driver"] is None
    assert payload["items"][1]["result"] is None


def test_result_response_requires_deterministic_position_order() -> None:
    with pytest.raises(
        ValidationError,
        match="ordered by position and session entry",
    ):
        SessionResultsResponse(
            session_id=210,
            snapshot=_available_snapshot(),
            items=(
                _result_item(session_entry_id=1002, position=None),
                _result_item(session_entry_id=1001, position=1),
            ),
        )


def test_result_response_rejects_duplicate_session_entries() -> None:
    item = _result_item(session_entry_id=1001, position=1)

    with pytest.raises(
        ValidationError,
        match="unique session entry IDs",
    ):
        SessionResultsResponse(
            session_id=210,
            snapshot=_available_snapshot(),
            items=(item, item),
        )


@pytest.mark.parametrize("team_color", ["3671C6", "#3671c6", "#3671C67"])
def test_result_entry_requires_canonical_team_color(team_color: str) -> None:
    with pytest.raises(ValidationError):
        _result_item(
            session_entry_id=1001,
            position=1,
            team_color_hex=team_color,
        )


def test_lap_query_defaults_and_range_validation() -> None:
    query = LapSummaryQuery()

    assert query.model_dump() == {
        "after_lap": None,
        "limit": 50,
        "lap_from": None,
        "lap_to": None,
        "stint_number": None,
        "include_deleted": True,
    }

    with pytest.raises(
        ValidationError,
        match="lap_from cannot be greater than lap_to",
    ):
        LapSummaryQuery(lap_from=20, lap_to=10)

    for values in (
        {"after_lap": -1},
        {"limit": 0},
        {"limit": 101},
        {"lap_from": 0},
        {"stint_number": 0},
    ):
        with pytest.raises(ValidationError):
            LapSummaryQuery(**values)


def test_lap_page_requires_cursor_presence_to_match_has_more() -> None:
    with pytest.raises(
        ValidationError,
        match="cursor presence must agree",
    ):
        LapSummaryPage(
            limit=50,
            has_more=True,
            next_after_lap=None,
        )

    with pytest.raises(
        ValidationError,
        match="cursor presence must agree",
    ):
        LapSummaryPage(
            limit=50,
            has_more=False,
            next_after_lap=10,
        )


def test_lap_response_serializes_complete_analysis_ready_summary() -> None:
    response = _lap_response(
        items=(_lap(lap_id=9_007_199_254_740_993, lap_number=1),),
    )

    payload = response.model_dump(mode="json")
    lap = payload["items"][0]

    assert payload["session_id"] == "210"
    assert payload["session_entry_id"] == "1001"
    assert lap["id"] == "9007199254740993"
    assert lap["lap_time_us"] == 95_543_210
    assert lap["compound"] == "SOFT"
    assert lap["deleted"] is False
    assert lap["is_accurate"] is True
    assert lap["source"] == "fastf1_archive"


@pytest.mark.parametrize(
    ("items", "filters", "page", "message"),
    [
        (
            (_lap(lap_id=2, lap_number=2), _lap(lap_id=1, lap_number=1)),
            None,
            None,
            "unique ascending lap numbers",
        ),
        (
            (_lap(lap_id=1, lap_number=1),),
            None,
            LapSummaryPage(limit=50, has_more=True, next_after_lap=2),
            "cursor must equal the last returned lap",
        ),
        (
            (_lap(lap_id=1, lap_number=1, stint_number=1),),
            LapSummaryFilters(
                lap_from=None,
                lap_to=None,
                stint_number=2,
                include_deleted=True,
            ),
            None,
            "does not match the response stint",
        ),
        (
            (_lap(lap_id=1, lap_number=1, deleted=True),),
            LapSummaryFilters(
                lap_from=None,
                lap_to=None,
                stint_number=None,
                include_deleted=False,
            ),
            None,
            "deleted lap cannot appear",
        ),
    ],
)
def test_lap_response_rejects_inconsistent_page_or_filters(
    items: tuple[LapSummary, ...],
    filters: LapSummaryFilters | None,
    page: LapSummaryPage | None,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _lap_response(
            items=items,
            filters=filters,
            page=page,
        )


def test_sporting_responses_require_an_available_snapshot() -> None:
    unavailable = SessionSnapshot(
        data_available=False,
        source=None,
        record_state=None,
        completed_at=None,
        source_updated_at=None,
    )

    with pytest.raises(
        ValidationError,
        match="result response requires an available snapshot",
    ):
        SessionResultsResponse(
            session_id=210,
            snapshot=unavailable,
            items=(),
        )

    with pytest.raises(
        ValidationError,
        match="lap response requires an available snapshot",
    ):
        _lap_response(
            items=(),
            snapshot=unavailable,
        )
