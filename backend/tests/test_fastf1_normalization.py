from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.ingestion.fastf1_normalization import (
    FastF1NormalizationError,
    normalize_fastf1_session,
)


def result_row(
    *,
    driver_number: object = "1",
    driver_id: object = "max_verstappen",
    abbreviation: object = "VER",
    full_name: object = "Max Verstappen",
    position: object = 1.0,
    classified_position: object = "1",
    laps: object = 57.0,
    time: object = timedelta(hours=1, minutes=30),
) -> dict[str, object]:
    return {
        "DriverNumber": driver_number,
        "BroadcastName": "M VERSTAPPEN",
        "Abbreviation": abbreviation,
        "DriverId": driver_id,
        "TeamName": "Red Bull Racing",
        "TeamColor": "3671C6",
        "TeamId": "red_bull",
        "FirstName": "Max",
        "LastName": "Verstappen",
        "FullName": full_name,
        "CountryCode": "NED",
        "Position": position,
        "ClassifiedPosition": classified_position,
        "GridPosition": 1.0,
        "Q1": pd.NaT,
        "Q2": pd.NaT,
        "Q3": pd.NaT,
        "Time": time,
        "Status": "Finished",
        "Points": 25.0,
        "Laps": laps,
    }


def lap_row(
    *,
    driver_number: object = "1",
    abbreviation: object = "VER",
    lap_number: object = 1.0,
) -> dict[str, object]:
    return {
        "Time": timedelta(minutes=2),
        "Driver": abbreviation,
        "DriverNumber": driver_number,
        "LapTime": timedelta(minutes=1, seconds=35, milliseconds=250),
        "LapNumber": lap_number,
        "Stint": 1.0,
        "PitOutTime": pd.NaT,
        "PitInTime": pd.NaT,
        "Sector1Time": timedelta(seconds=30),
        "Sector2Time": timedelta(seconds=32),
        "Sector3Time": timedelta(seconds=33, milliseconds=250),
        "Sector1SessionTime": timedelta(minutes=1),
        "Sector2SessionTime": timedelta(minutes=1, seconds=32),
        "Sector3SessionTime": timedelta(minutes=2, seconds=5, milliseconds=250),
        "SpeedI1": 287.5,
        "SpeedI2": pd.NA,
        "SpeedFL": 301.0,
        "SpeedST": 315.2,
        "IsPersonalBest": True,
        "Compound": "SOFT",
        "TyreLife": 1.0,
        "FreshTyre": True,
        "LapStartTime": timedelta(seconds=24, milliseconds=750),
        "TrackStatus": "21",
        "Position": 1.0,
        "Deleted": pd.NA,
        "DeletedReason": "",
        "FastF1Generated": False,
        "IsAccurate": True,
    }


def test_normalizes_complete_race_snapshot() -> None:
    winner = result_row()
    runner_up = result_row(
        driver_number="4",
        driver_id="norris",
        abbreviation="NOR",
        full_name="Lando Norris",
        position=2.0,
        classified_position="2",
        time=timedelta(seconds=5, milliseconds=250),
    )
    runner_up.update(
        {
            "BroadcastName": "L NORRIS",
            "FirstName": "Lando",
            "LastName": "Norris",
            "CountryCode": "GBR",
            "TeamName": "McLaren",
            "TeamColor": "FF8700",
            "TeamId": "mclaren",
            "GridPosition": 2.0,
            "Points": 18.0,
        }
    )
    lapped = result_row(
        driver_number="27",
        driver_id=None,
        abbreviation="HUL",
        full_name="Nico Hulkenberg",
        position=3.0,
        classified_position="R",
        laps=55.0,
        time=pd.NaT,
    )
    lapped.update(
        {
            "BroadcastName": "N HULKENBERG",
            "FirstName": "Nico",
            "LastName": "Hulkenberg",
            "CountryCode": "GER",
            "TeamName": "Haas F1 Team",
            "TeamColor": "B6BABD",
            "TeamId": "haas",
            "GridPosition": 3.0,
            "Points": 15.0,
        }
    )

    normalized = normalize_fastf1_session(
        pd.DataFrame([winner, runner_up, lapped]),
        pd.DataFrame([lap_row()]),
        session_name="Race",
    )

    assert [driver.jolpica_driver_id for driver in normalized.drivers] == [
        "max_verstappen",
        "norris",
    ]
    assert [entry.entry_key for entry in normalized.entries] == [
        "driver:jolpica:max_verstappen",
        "driver:jolpica:norris",
        "car-number:27",
    ]
    assert normalized.entries[2].jolpica_driver_id is None
    assert normalized.entries[2].source == "fastf1_archive"
    assert normalized.entries[2].record_state == "finalized"

    first, second, third = normalized.results
    assert first.elapsed_time_us == 5_400_000_000
    assert first.gap_to_leader_us == 0
    assert first.gap_to_leader_laps == 0
    assert first.points == Decimal("25.0")
    assert second.elapsed_time_us is None
    assert second.gap_to_leader_us == 5_250_000
    assert second.gap_to_leader_laps == 0
    assert third.gap_to_leader_us is None
    assert third.gap_to_leader_laps == 2
    assert third.classified_position == "R"

    assert len(normalized.laps) == 1
    lap = normalized.laps[0]
    assert lap.entry_key == "driver:jolpica:max_verstappen"
    assert lap.lap_number == 1
    assert lap.lap_time_us == 95_250_000
    assert lap.sector_3_time_us == 33_250_000
    assert lap.speed_i2_kph is None
    assert lap.track_status == "21"
    assert lap.deleted is None
    assert lap.deleted_reason is None


def test_preserves_unknown_historical_personal_best_flag() -> None:
    historical_lap = lap_row()
    historical_lap["IsPersonalBest"] = pd.NA

    normalized = normalize_fastf1_session(
        pd.DataFrame([result_row()]),
        pd.DataFrame([historical_lap]),
        session_name="Race",
    )

    assert normalized.laps[0].is_personal_best is None


def test_normalizes_qualifying_times_without_race_gap_semantics() -> None:
    qualifying_result = result_row(time=timedelta(minutes=1, seconds=29))
    qualifying_result.update(
        {
            "Q1": timedelta(minutes=1, seconds=30, milliseconds=100),
            "Q2": timedelta(minutes=1, seconds=29, milliseconds=500),
            "Q3": timedelta(minutes=1, seconds=29),
            "Laps": pd.NA,
        }
    )

    normalized = normalize_fastf1_session(
        pd.DataFrame([qualifying_result]),
        pd.DataFrame(),
        session_name="Qualifying",
    )

    result = normalized.results[0]
    assert result.q1_time_us == 90_100_000
    assert result.q2_time_us == 89_500_000
    assert result.q3_time_us == 89_000_000
    assert result.elapsed_time_us is None
    assert result.gap_to_leader_us is None
    assert result.gap_to_leader_laps is None
    assert normalized.laps == ()


def test_canonicalizes_driver_ids_and_racing_numbers() -> None:
    row = result_row(driver_number="01", driver_id=" MAX_VERSTAPPEN ")

    normalized = normalize_fastf1_session(
        pd.DataFrame([row]),
        pd.DataFrame(),
        session_name="Race",
    )

    assert normalized.drivers[0].jolpica_driver_id == "max_verstappen"
    assert normalized.entries[0].entry_key == "driver:jolpica:max_verstappen"
    assert normalized.entries[0].racing_number == "1"


def test_treats_fastf1_string_nan_driver_ids_as_missing() -> None:
    first = result_row(
        driver_number="34",
        driver_id="nan",
        abbreviation="LAT",
        full_name="Nicholas Latifi",
    )
    second = result_row(
        driver_number="36",
        driver_id=" NAN ",
        abbreviation="GIO",
        full_name="Antonio Giovinazzi",
        position=2,
    )

    normalized = normalize_fastf1_session(
        pd.DataFrame([first, second]),
        pd.DataFrame(),
        session_name="Practice 1",
    )

    assert normalized.drivers == ()
    assert [entry.entry_key for entry in normalized.entries] == [
        "car-number:34",
        "car-number:36",
    ]
    assert all(
        entry.jolpica_driver_id is None
        for entry in normalized.entries
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                result_row(driver_number="4", driver_id=None),
                result_row(
                    driver_number=4.0,
                    driver_id="norris",
                    abbreviation="NOR",
                    position=2,
                    time=timedelta(seconds=1),
                ),
            ],
            "duplicates racing number",
        ),
        (
            [result_row(driver_number=None, driver_id=None)],
            "neither a driver ID nor a racing number",
        ),
        (
            [result_row(position=2)],
            "exactly one first-place result",
        ),
    ],
)
def test_rejects_ambiguous_or_incomplete_result_identity(
    rows: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(FastF1NormalizationError, match=message):
        normalize_fastf1_session(
            pd.DataFrame(rows),
            pd.DataFrame(),
            session_name="Race",
        )


def test_rejects_non_integral_lap_numbers() -> None:
    with pytest.raises(FastF1NormalizationError, match="LapNumber must be an integer"):
        normalize_fastf1_session(
            pd.DataFrame([result_row()]),
            pd.DataFrame([lap_row(lap_number=1.5)]),
            session_name="Race",
        )


def test_rejects_duplicate_lap_natural_keys() -> None:
    with pytest.raises(FastF1NormalizationError, match="duplicates lap 1"):
        normalize_fastf1_session(
            pd.DataFrame([result_row()]),
            pd.DataFrame([lap_row(), lap_row()]),
            session_name="Race",
        )


def test_rejects_laps_that_do_not_match_an_entry() -> None:
    with pytest.raises(FastF1NormalizationError, match="unknown racing number"):
        normalize_fastf1_session(
            pd.DataFrame([result_row()]),
            pd.DataFrame([lap_row(driver_number="4", abbreviation="NOR")]),
            session_name="Race",
        )


def test_rejects_invalid_required_boolean() -> None:
    invalid_lap = lap_row()
    invalid_lap["IsAccurate"] = 1

    with pytest.raises(FastF1NormalizationError, match="IsAccurate must be a boolean"):
        normalize_fastf1_session(
            pd.DataFrame([result_row()]),
            pd.DataFrame([invalid_lap]),
            session_name="Race",
        )


def test_rejects_sub_microsecond_duration() -> None:
    row = result_row(time=pd.to_timedelta(1, unit="ns"))

    with pytest.raises(FastF1NormalizationError, match="whole microseconds"):
        normalize_fastf1_session(
            pd.DataFrame([row]),
            pd.DataFrame(),
            session_name="Race",
        )


def test_a_lap_run_before_the_session_start_is_kept() -> None:
    """FastF1 counts from the official start, so an earlier lap is negative.

    In the 2025 Spanish Grand Prix third practice thirteen cars were already
    on track when the session clock reached zero. Rejecting their negative
    offsets cost all 312 laps of the session.
    """
    early = lap_row()
    early["Time"] = timedelta(seconds=-75, milliseconds=-195)
    early["LapStartTime"] = timedelta(seconds=-134)
    early["Sector1SessionTime"] = timedelta(seconds=-120)
    early["Sector2SessionTime"] = timedelta(seconds=-100)
    early["PitOutTime"] = timedelta(seconds=-140)

    session = normalize_fastf1_session(
        pd.DataFrame([result_row()]),
        pd.DataFrame([early]),
        session_name="Practice 3",
    )

    lap = session.laps[0]
    assert lap.session_time_us == -75_195_000
    assert lap.lap_start_time_us == -134_000_000
    assert lap.sector_1_session_time_us == -120_000_000
    assert lap.pit_out_time_us == -140_000_000


def test_a_negative_duration_is_still_rejected() -> None:
    # An instant may precede the session start; a lap cannot take less than no
    # time to complete, and neither can a sector.
    for field in ("LapTime", "Sector1Time", "Sector2Time", "Sector3Time"):
        impossible = lap_row()
        impossible[field] = timedelta(seconds=-1)

        with pytest.raises(FastF1NormalizationError, match="must not be negative"):
            normalize_fastf1_session(
                pd.DataFrame([result_row()]),
                pd.DataFrame([impossible]),
                session_name="Practice 3",
            )
