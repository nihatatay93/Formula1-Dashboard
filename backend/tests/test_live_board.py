"""Board tests, grounded in the recorded Hungarian Grand Prix 2026 qualifying."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.board import (
    MAX_RACE_CONTROL_MESSAGES,
    SEGMENT_STATUS,
    UNKNOWN_SEGMENT_STATUS,
    board_to_dict,
    build_board,
)
from app.live.current_view import LiveCurrentView
from app.live.frames import LiveFrameRejectedError, normalize_frame

FIXTURE = Path(__file__).parent / "fixtures" / "live_signalr_qualifying.jsonl"
BASE = datetime(2026, 7, 25, 14, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def recorded_topics() -> dict[str, object]:
    view = LiveCurrentView()
    for index, line in enumerate(
        FIXTURE.read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        try:
            view.apply(
                normalize_frame(
                    record.get("topic"),
                    record.get("payload"),
                    received_at=BASE + timedelta(seconds=index),
                    initial=bool(record.get("initial")),
                    feed_timestamp=record.get("timestamp"),
                )
            )
        except LiveFrameRejectedError:
            continue
    return {topic: state.payload for topic, state in view.topics.items()}


class TestRecordedSession:
    def test_session_header_comes_from_the_feed(self, recorded_topics) -> None:
        board = build_board(recorded_topics)

        assert board.meeting_name == "Hungarian Grand Prix"
        assert board.session_type == "Qualifying"
        assert board.track_status == "AllClear"
        assert board.track_status_code == "1"

    def test_every_driver_appears_once(self, recorded_topics) -> None:
        board = build_board(recorded_topics)

        assert len(board.drivers) == 22
        numbers = [row.racing_number for row in board.drivers]
        assert len(set(numbers)) == 22

    def test_drivers_are_ordered_by_position(self, recorded_topics) -> None:
        board = build_board(recorded_topics)

        positions = [row.position for row in board.drivers if row.position]
        assert positions == sorted(positions)
        assert board.drivers[0].position == 1

    def test_identity_is_joined_from_the_driver_list(self, recorded_topics) -> None:
        leader = build_board(recorded_topics).drivers[0]

        # TimingData carries no names; they come from DriverList.
        assert leader.tla
        assert leader.full_name
        assert leader.team_name
        assert len(leader.team_colour) == 6

    def test_qualifying_best_lap_and_gap_are_resolved(self, recorded_topics) -> None:
        board = build_board(recorded_topics)
        timed = [row for row in board.drivers if row.best_lap]

        assert timed, "a recorded qualifying session has lap times"
        assert ":" in timed[0].best_lap

    def test_sectors_are_exposed_with_their_best_flags(self, recorded_topics) -> None:
        board = build_board(recorded_topics)
        with_sectors = [row for row in board.drivers if row.sectors]

        assert with_sectors
        cell = with_sectors[0].sectors[0]
        assert isinstance(cell.personal_best, bool)
        assert isinstance(cell.overall_best, bool)

    def test_mini_sector_segments_are_exposed(self, recorded_topics) -> None:
        board = build_board(recorded_topics)
        segmented = [
            cell
            for row in board.drivers
            for cell in row.sectors
            if cell.segments
        ]

        assert segmented
        # The recording's three sectors hold 7, 9 and 6 micro-sectors.
        assert {len(cell.segments) for cell in segmented} == {7, 9, 6}
        assert {name for cell in segmented for name in cell.segments} <= set(
            SEGMENT_STATUS.values()
        ) | {UNKNOWN_SEGMENT_STATUS}
        # The recording contains pit-lane and overall-fastest micro-sectors.
        rendered = {name for cell in segmented for name in cell.segments}
        assert "pit" in rendered
        assert "purple" in rendered

    def test_tyre_comes_from_the_latest_stint(self, recorded_topics) -> None:
        board = build_board(recorded_topics)
        shod = [row for row in board.drivers if row.compound]

        assert shod
        assert shod[0].compound in {
            "SOFT",
            "MEDIUM",
            "HARD",
            "INTERMEDIATE",
            "WET",
            "UNKNOWN",
        }

    def test_race_control_is_newest_first_and_bounded(self, recorded_topics) -> None:
        board = build_board(recorded_topics)

        assert board.race_control
        assert len(board.race_control) <= MAX_RACE_CONTROL_MESSAGES
        assert board.race_control[0].message

    def test_weather_drops_the_keyframe_marker(self, recorded_topics) -> None:
        board = build_board(recorded_topics)

        assert "_kf" not in board.weather
        assert board.weather

    def test_the_board_is_json_safe(self, recorded_topics) -> None:
        rendered = json.dumps(board_to_dict(build_board(recorded_topics)))

        assert '"drivers"' in rendered
        assert len(rendered) > 1000


class TestRaceShapes:
    """A race carries different fields from qualifying."""

    def base(self) -> dict[str, object]:
        return {
            "SessionInfo": {"Meeting": {"Name": "Race GP"}, "Type": "Race"},
            "DriverList": {
                "1": {
                    "RacingNumber": "1",
                    "Tla": "NOR",
                    "FullName": "Lando NORRIS",
                    "TeamName": "McLaren",
                    "TeamColour": "F47600",
                }
            },
            "TimingData": {
                "Lines": {
                    "1": {
                        "Position": "1",
                        "Line": 1,
                        "GapToLeader": "+1.204",
                        "IntervalToPositionAhead": {"Value": "+0.512"},
                        "LastLapTime": {
                            "Value": "1:23.625",
                            "PersonalFastest": True,
                            "OverallFastest": False,
                        },
                        "BestLapTime": {"Value": "1:22.491", "Lap": 64},
                        "NumberOfLaps": 70,
                        "NumberOfPitStops": 3,
                        "InPit": False,
                    }
                }
            },
            "LapCount": {"CurrentLap": 70, "TotalLaps": 70},
        }

    def test_race_gaps_and_counts_are_used(self) -> None:
        row = build_board(self.base()).drivers[0]

        assert row.gap_to_leader == "+1.204"
        assert row.interval == "+0.512"
        assert row.best_lap == "1:22.491"
        assert row.last_lap == "1:23.625"
        assert row.last_lap_personal_best is True
        assert row.pit_stops == 3
        assert row.laps == 70

    def test_lap_count_reaches_the_header(self) -> None:
        board = build_board(self.base())

        assert (board.current_lap, board.total_laps) == (70, 70)


class TestQualifyingShapes:
    def board_with(self, part: int) -> object:
        topics = {
            "TimingData": {
                "SessionPart": part,
                "Lines": {
                    "1": {
                        "Position": "1",
                        "Line": 1,
                        "BestLapTimes": [
                            {"Value": "1:18.277"},
                            {"Value": "1:17.456"},
                            {},
                        ],
                        "Stats": [
                            {"TimeDiffToFastest": "+0.500"},
                            {"TimeDiffToFastest": "+0.100"},
                            {"TimeDiffToFastest": ""},
                        ],
                    }
                },
            }
        }
        return build_board(topics).drivers[0]

    def test_the_current_session_part_selects_the_time(self) -> None:
        assert self.board_with(1).best_lap == "1:18.277"
        assert self.board_with(2).best_lap == "1:17.456"

    def test_the_current_part_selects_the_gap(self) -> None:
        assert self.board_with(1).gap_to_leader == "+0.500"
        assert self.board_with(2).gap_to_leader == "+0.100"

    def test_an_empty_current_part_falls_back_to_the_last_time_set(self) -> None:
        # Q3 has no time yet; showing the Q2 time beats showing nothing.
        assert self.board_with(3).best_lap == "1:17.456"


class TestDefensiveDerivation:
    def test_an_empty_state_produces_an_empty_board(self) -> None:
        board = build_board({})

        assert board.drivers == ()
        assert board.race_control == ()
        assert board.meeting_name == ""

    @pytest.mark.parametrize("garbage", [None, 7, "text", [1, 2], {"Lines": 5}])
    def test_hostile_timing_payloads_do_not_raise(self, garbage: object) -> None:
        assert build_board({"TimingData": garbage}).drivers == ()

    def test_a_driver_missing_from_the_driver_list_still_appears(self) -> None:
        board = build_board(
            {"TimingData": {"Lines": {"44": {"Position": "5", "Line": 5}}}}
        )

        assert board.drivers[0].racing_number == "44"
        assert board.drivers[0].tla == ""

    def test_rows_without_a_position_sort_after_positioned_rows(self) -> None:
        board = build_board(
            {
                "TimingData": {
                    "Lines": {
                        "9": {"Line": 2},
                        "1": {"Position": "1", "Line": 1},
                    }
                }
            }
        )

        assert [row.racing_number for row in board.drivers] == ["1", "9"]

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ({"Retired": True}, "Retired"),
            ({"Stopped": True}, "Stopped"),
            ({"InPit": True}, "In pit"),
            ({"PitOut": True}, "Out lap"),
            ({"KnockedOut": True}, "Knocked out"),
            ({}, "On track"),
        ],
    )
    def test_driver_status_is_derived(self, line: dict, expected: str) -> None:
        board = build_board({"TimingData": {"Lines": {"1": line}}})

        assert board.drivers[0].status == expected

    def test_stints_delivered_as_an_index_keyed_mapping_are_handled(self) -> None:
        # Deltas can arrive keyed by index rather than as a list.
        board = build_board(
            {
                "TimingData": {"Lines": {"1": {"Position": "1"}}},
                "TimingAppData": {
                    "Lines": {
                        "1": {
                            "Stints": {
                                "0": {"Compound": "MEDIUM", "TotalLaps": 4},
                                "1": {"Compound": "SOFT", "TotalLaps": 9},
                            }
                        }
                    }
                },
            }
        )

        assert board.drivers[0].compound == "SOFT"
        assert board.drivers[0].tyre_age == 9

    def test_race_control_accepts_an_index_keyed_mapping(self) -> None:
        board = build_board(
            {
                "RaceControlMessages": {
                    "Messages": {
                        "0": {"Message": "first", "Lap": 1},
                        "1": {"Message": "second", "Lap": 2},
                    }
                }
            }
        )

        assert [item.message for item in board.race_control] == ["second", "first"]


class TestMiniSectorSegments:
    """Status codes were derived from the recording; see ``SEGMENT_STATUS``."""

    def build(self, segments: object) -> tuple[str, ...]:
        board = build_board(
            {"TimingData": {"Lines": {"1": {"Sectors": [{"Segments": segments}]}}}}
        )
        return board.drivers[0].sectors[0].segments

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (0, "pending"),
            (2048, "yellow"),
            (2049, "green"),
            (2051, "purple"),
            (2064, "pit"),
        ],
    )
    def test_observed_codes_map_to_their_meaning(self, code: int, expected: str) -> None:
        assert self.build([{"Status": code}]) == (expected,)

    def test_order_is_preserved(self) -> None:
        assert self.build(
            [{"Status": 2064}, {"Status": 2049}, {"Status": 2051}, {"Status": 0}]
        ) == ("pit", "green", "purple", "pending")

    def test_segments_delivered_as_an_index_keyed_mapping_are_ordered(self) -> None:
        # A delta patches the array by index, and "10" must sort after "2".
        codes = {str(index): {"Status": 2049} for index in range(11)}
        codes["2"] = {"Status": 2051}
        codes["10"] = {"Status": 2048}
        segments = self.build(codes)

        assert len(segments) == 11
        assert segments[2] == "purple"
        assert segments[10] == "yellow"

    @pytest.mark.parametrize(
        "segments",
        [None, "not-a-list", 7, [None], ["text"], [{}], [{"Status": None}]],
    )
    def test_unusable_segment_payloads_never_raise(self, segments: object) -> None:
        # The feed is untrusted; an unreadable entry renders neutrally.
        assert set(self.build(segments)) <= {UNKNOWN_SEGMENT_STATUS}

    def test_an_unobserved_code_is_not_guessed_at(self) -> None:
        assert 2050 not in SEGMENT_STATUS
        assert self.build([{"Status": 2050}]) == (UNKNOWN_SEGMENT_STATUS,)

    def test_segments_reach_the_serialised_board(self) -> None:
        board = build_board(
            {
                "TimingData": {
                    "Lines": {"1": {"Sectors": [{"Segments": [{"Status": 2051}]}]}}
                }
            }
        )
        rendered = board_to_dict(board)

        assert rendered["drivers"][0]["sectors"][0]["segments"] == ["purple"]
        json.dumps(rendered)
