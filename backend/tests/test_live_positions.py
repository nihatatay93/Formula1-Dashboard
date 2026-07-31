"""Position movement across a live session."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.board import build_board
from app.live.positions import RECENT_MOVE_WINDOW, PositionTracker

BASE = datetime(2026, 7, 25, 14, 0, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "live_signalr_qualifying.jsonl"


def lines(**positions: int) -> dict[str, object]:
    return {
        "Lines": {
            number: {"Position": str(value)} for number, value in positions.items()
        }
    }


class TestTracking:
    def test_the_first_sighting_becomes_the_baseline(self) -> None:
        tracker = PositionTracker()

        tracker.observe(lines(a=5), received_at=BASE)

        movement = tracker.movement("a", now=BASE)
        assert movement is not None
        assert movement.baseline == 5
        assert movement.current == 5
        assert movement.places_gained == 0

    def test_moving_up_the_order_is_a_positive_gain(self) -> None:
        # 15th to 3rd is a gain of twelve, not a change of minus twelve.
        tracker = PositionTracker()
        tracker.observe(lines(a=15), received_at=BASE)

        tracker.observe(lines(a=3), received_at=BASE + timedelta(seconds=1))

        movement = tracker.movement("a", now=BASE + timedelta(seconds=1))
        assert movement is not None
        assert movement.places_gained == 12
        assert movement.recent == "up"

    def test_losing_places_is_negative(self) -> None:
        tracker = PositionTracker()
        tracker.observe(lines(a=2), received_at=BASE)

        tracker.observe(lines(a=9), received_at=BASE + timedelta(seconds=1))

        movement = tracker.movement("a", now=BASE + timedelta(seconds=1))
        assert movement is not None
        assert movement.places_gained == -7
        assert movement.recent == "down"

    def test_a_recent_move_stops_being_recent(self) -> None:
        tracker = PositionTracker()
        tracker.observe(lines(a=4), received_at=BASE)
        moved = BASE + timedelta(seconds=1)
        tracker.observe(lines(a=3), received_at=moved)

        assert tracker.movement("a", now=moved).recent == "up"
        inside = moved + RECENT_MOVE_WINDOW
        assert tracker.movement("a", now=inside).recent == "up"
        outside = moved + RECENT_MOVE_WINDOW + timedelta(seconds=1)
        assert tracker.movement("a", now=outside).recent is None
        # The net movement outlives the flag.
        assert tracker.movement("a", now=outside).places_gained == 1

    def test_a_reconnect_snapshot_does_not_reset_the_baseline(self) -> None:
        """The critical case: a reconnect resends full state mid-session.

        Treating that as a fresh sighting would rebaseline every driver and
        silently erase the session's movement.
        """
        tracker = PositionTracker()
        tracker.observe(lines(a=20), received_at=BASE)
        tracker.observe(lines(a=8), received_at=BASE + timedelta(seconds=30))

        # A reconnect delivers current positions as a full-state frame.
        tracker.observe(lines(a=8), received_at=BASE + timedelta(seconds=60))

        movement = tracker.movement("a", now=BASE + timedelta(seconds=60))
        assert movement is not None
        assert movement.baseline == 20, "the reconnect rebaselined the driver"
        assert movement.places_gained == 12

    def test_an_unchanged_position_is_not_a_move(self) -> None:
        tracker = PositionTracker()
        tracker.observe(lines(a=6), received_at=BASE)

        tracker.observe(lines(a=6), received_at=BASE + timedelta(seconds=5))

        assert tracker.movement("a", now=BASE + timedelta(seconds=5)).recent is None

    def test_a_driver_never_seen_has_no_movement(self) -> None:
        assert PositionTracker().movement("a", now=BASE) is None

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            "text",
            {"Lines": "text"},
            {"Lines": {"a": "text"}},
            {"Lines": {"a": {"Position": None}}},
            {"Lines": {"a": {"Position": "not-a-number"}}},
            {"Lines": {"a": {"Position": 0}}},
            {"Lines": {"a": {"Position": -3}}},
            {"Lines": {"a": {"Position": True}}},
        ],
    )
    def test_unusable_payloads_are_ignored(self, payload: object) -> None:
        # The feed is untrusted, and a zero or negative position is not a place.
        tracker = PositionTracker()

        tracker.observe(payload, received_at=BASE)

        assert len(tracker) == 0

    def test_each_driver_is_tracked_independently(self) -> None:
        tracker = PositionTracker()
        tracker.observe(lines(a=1, b=2), received_at=BASE)

        tracker.observe(lines(a=2, b=1), received_at=BASE + timedelta(seconds=1))

        now = BASE + timedelta(seconds=1)
        assert tracker.movement("a", now=now).places_gained == -1
        assert tracker.movement("b", now=now).places_gained == 1


class TestOnTheBoard:
    def test_movement_reaches_the_rows(self) -> None:
        tracker = PositionTracker()
        tracker.observe(lines(**{"1": 12}), received_at=BASE)
        tracker.observe(lines(**{"1": 4}), received_at=BASE + timedelta(seconds=1))

        board = build_board(
            {"TimingData": {"Lines": {"1": {"Position": "4"}}}},
            positions=tracker,
            now=BASE + timedelta(seconds=1),
        )

        row = board.drivers[0]
        assert row.places_gained == 8
        assert row.position_baseline == 12
        assert row.recent_move == "up"

    def test_a_board_without_a_tracker_reports_no_movement(self) -> None:
        # The board stays complete when history is not supplied.
        board = build_board({"TimingData": {"Lines": {"1": {"Position": "4"}}}})

        row = board.drivers[0]
        assert row.places_gained is None
        assert row.position_baseline is None
        assert row.recent_move == ""

    def test_movement_survives_the_recorded_session(self) -> None:
        """Against the real recording, not a hand-built payload."""
        from app.live.frames import LiveFrameRejectedError, normalize_frame

        tracker = PositionTracker()
        last = BASE
        for index, line in enumerate(
            FIXTURE.read_text(encoding="utf-8").splitlines()
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            last = BASE + timedelta(seconds=index)
            try:
                frame = normalize_frame(
                    record.get("topic"),
                    record.get("payload"),
                    received_at=last,
                    initial=bool(record.get("initial")),
                    feed_timestamp=record.get("timestamp"),
                )
            except LiveFrameRejectedError:
                continue
            if frame.topic == "TimingData":
                tracker.observe(frame.payload, received_at=frame.received_at)

        assert len(tracker) == 22
        movements = [
            tracker.movement(str(number), now=last) for number in range(1, 100)
        ]
        seen = [item for item in movements if item is not None]
        assert seen, "no driver numbers resolved against the recording"
        assert all(item.baseline >= 1 for item in seen)
