from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.live.current_view import LiveCurrentView, rebuild_from_log
from app.live.frames import LiveFrame, normalize_frame
from app.live.session_log import LiveSessionLog

BASE_TIME = datetime(2026, 8, 21, 13, 0, 0, tzinfo=UTC)


def frame(
    sequence: int,
    topic: str = "TimingData",
    *,
    position: int | None = None,
    offset_seconds: int = 0,
) -> LiveFrame:
    return normalize_frame(
        topic,
        {"Position": sequence if position is None else position},
        received_at=BASE_TIME + timedelta(seconds=offset_seconds),
        sequence=sequence,
    )


def test_applying_frames_tracks_latest_state_per_topic() -> None:
    view = LiveCurrentView()

    assert view.apply(frame(1, "TimingData")) is True
    assert view.apply(frame(1, "TrackStatus")) is True
    assert view.apply(frame(2, "TimingData", position=9)) is True

    assert view.applied_frames == 3
    assert view.last_sequence("TimingData") == 2
    assert view.last_sequence("TrackStatus") == 1
    assert view.topics["TimingData"].payload == {"Position": 9}


def test_replayed_sequence_is_discarded_rather_than_overwriting_newer_state() -> None:
    view = LiveCurrentView()
    view.apply(frame(5, position=5))

    assert view.apply(frame(5, position=99)) is False
    assert view.apply(frame(3, position=99)) is False

    assert view.discarded_frames == 2
    assert view.topics["TimingData"].payload == {"Position": 5}
    assert view.last_sequence("TimingData") == 5


def test_dedup_is_scoped_per_topic() -> None:
    view = LiveCurrentView()
    view.apply(frame(10, "TimingData"))

    # A lower sequence on a different topic is not a replay.
    assert view.apply(frame(1, "DriverList")) is True
    assert view.discarded_frames == 0


def test_unknown_topic_has_no_recorded_sequence() -> None:
    assert LiveCurrentView().last_sequence("TimingData") is None


def test_apply_all_returns_the_accepted_count() -> None:
    view = LiveCurrentView()

    accepted = view.apply_all([frame(1), frame(2), frame(2), frame(1)])

    assert accepted == 2
    assert view.discarded_frames == 2


def test_latest_received_at_is_the_newest_applied_frame() -> None:
    view = LiveCurrentView()
    view.apply(frame(1, "TimingData", offset_seconds=0))
    view.apply(frame(1, "TrackStatus", offset_seconds=30))

    assert view.latest_received_at() == BASE_TIME + timedelta(seconds=30)


def test_snapshot_of_an_empty_view_is_json_safe() -> None:
    snapshot = LiveCurrentView().snapshot()

    assert snapshot == {
        "latest_received_at": None,
        "applied_frames": 0,
        "topics": {},
    }


def test_snapshot_exposes_sorted_topics_with_sequences() -> None:
    view = LiveCurrentView()
    view.apply(frame(4, "TrackStatus"))
    view.apply(frame(7, "DriverList"))

    snapshot = view.snapshot()

    assert list(snapshot["topics"]) == ["DriverList", "TrackStatus"]
    assert snapshot["topics"]["DriverList"]["sequence"] == 7
    assert snapshot["applied_frames"] == 2


def test_rebuild_from_log_restores_the_view_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        log.append(frame(1, "TimingData"))
        log.append(frame(2, "TimingData", position=8))
        log.append(frame(1, "TrackStatus"))

    view = rebuild_from_log(path)

    assert view.last_sequence("TimingData") == 2
    assert view.topics["TimingData"].payload == {"Position": 8}
    assert view.last_sequence("TrackStatus") == 1


def test_rebuild_from_a_missing_log_yields_an_empty_view(tmp_path: Path) -> None:
    view = rebuild_from_log(tmp_path / "absent.jsonl")

    assert view.applied_frames == 0
    assert view.snapshot()["topics"] == {}
