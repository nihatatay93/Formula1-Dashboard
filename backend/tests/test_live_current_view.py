from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.live.current_view import LiveCurrentView, merge_delta, rebuild_from_log
from app.live.frames import LiveFrame, normalize_frame
from app.live.session_log import LiveSessionLog

BASE_TIME = datetime(2026, 7, 25, 14, 0, 0, tzinfo=UTC)


def frame(
    payload: dict[str, object],
    topic: str = "TimingData",
    *,
    initial: bool = False,
    offset_seconds: int = 0,
) -> LiveFrame:
    return normalize_frame(
        topic,
        payload,
        received_at=BASE_TIME + timedelta(seconds=offset_seconds),
        initial=initial,
    )


class TestMergeDelta:
    def test_mapping_patch_merges_key_by_key(self) -> None:
        merged = merge_delta({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 9}})

        assert merged == {"a": 1, "b": {"c": 9, "d": 3}}

    def test_scalar_patch_replaces(self) -> None:
        assert merge_delta({"a": 1}, {"a": 2}) == {"a": 2}
        assert merge_delta("old", "new") == "new"

    def test_index_keyed_patch_updates_a_list_without_retyping_it(self) -> None:
        merged = merge_delta({"s": [{"v": 1}, {"v": 2}]}, {"s": {"1": {"v": 9}}})

        assert merged == {"s": [{"v": 1}, {"v": 9}]}
        assert isinstance(merged["s"], list)

    def test_non_numeric_key_against_a_list_is_ignored(self) -> None:
        assert merge_delta({"s": [1, 2]}, {"s": {"x": 9}}) == {"s": [1, 2]}

    def test_out_of_range_index_is_dropped(self) -> None:
        assert merge_delta({"s": [1]}, {"s": {"5": 9}}) == {"s": [1]}

    def test_patch_against_absent_state_creates_it(self) -> None:
        assert merge_delta(None, {"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_neither_argument_is_mutated(self) -> None:
        target = {"a": {"b": 1}, "s": [{"v": 1}]}
        patch = {"a": {"b": 2}, "s": {"0": {"v": 2}}}

        merge_delta(target, patch)

        assert target == {"a": {"b": 1}, "s": [{"v": 1}]}
        assert patch == {"a": {"b": 2}, "s": {"0": {"v": 2}}}

    def test_a_list_patch_replaces_a_list_outright(self) -> None:
        assert merge_delta({"s": [1, 2, 3]}, {"s": [9]}) == {"s": [9]}


def test_initial_frame_seeds_topic_state() -> None:
    view = LiveCurrentView()

    assert view.apply(frame({"Lines": {"1": {"Position": "1"}}}, initial=True)) is True

    state = view.topics["TimingData"]
    assert state.snapshots == 1
    assert state.updates == 0
    assert view.applied_frames == 1


def test_delta_merges_into_existing_state() -> None:
    view = LiveCurrentView()
    view.apply(
        frame({"Lines": {"1": {"Position": "1"}, "2": {"Position": "2"}}}, initial=True)
    )

    assert view.apply(frame({"Lines": {"1": {"Position": "3"}}})) is True

    lines = view.topics["TimingData"].payload["Lines"]
    assert lines == {"1": {"Position": "3"}, "2": {"Position": "2"}}
    assert view.topics["TimingData"].updates == 1


def test_a_delta_before_any_snapshot_seeds_state() -> None:
    view = LiveCurrentView()

    assert view.apply(frame({"Lines": {"1": {"Position": "1"}}})) is True

    assert view.topics["TimingData"].snapshots == 0


def test_reapplied_delta_reports_no_change() -> None:
    view = LiveCurrentView()
    view.apply(frame({"Lines": {"1": {"Position": "1"}}}, initial=True))
    delta = frame({"Lines": {"1": {"Position": "5"}}})

    assert view.apply(delta) is True
    assert view.apply(delta) is False

    assert view.unchanged_frames == 1
    assert view.topics["TimingData"].payload["Lines"]["1"]["Position"] == "5"


def test_an_identical_snapshot_reports_no_change() -> None:
    view = LiveCurrentView()
    snapshot = frame({"Status": "1"}, topic="TrackStatus", initial=True)
    view.apply(snapshot)

    assert view.apply(snapshot) is False
    assert view.topics["TrackStatus"].snapshots == 2


def test_state_is_tracked_per_topic() -> None:
    view = LiveCurrentView()

    view.apply(frame({"Lines": {"1": {}}}, initial=True))
    view.apply(frame({"Status": "1"}, topic="TrackStatus", initial=True))

    assert set(view.topics) == {"TimingData", "TrackStatus"}
    assert view.applied_frames == 2


def test_apply_all_returns_the_changed_count() -> None:
    view = LiveCurrentView()
    first = frame({"Status": "1"}, topic="TrackStatus", initial=True)
    repeat = frame({"Status": "1"}, topic="TrackStatus")

    assert view.apply_all([first, repeat, repeat]) == 1
    assert view.unchanged_frames == 2


def test_latest_received_at_is_the_newest_applied_frame() -> None:
    view = LiveCurrentView()
    view.apply(frame({"Lines": {}}, initial=True, offset_seconds=0))
    view.apply(
        frame({"Status": "1"}, topic="TrackStatus", initial=True, offset_seconds=30)
    )

    assert view.latest_received_at() == BASE_TIME + timedelta(seconds=30)


def test_snapshot_of_an_empty_view_is_json_safe() -> None:
    assert LiveCurrentView().snapshot() == {
        "latest_received_at": None,
        "applied_frames": 0,
        "topics": {},
    }


def test_snapshot_exposes_sorted_topics_with_counters() -> None:
    view = LiveCurrentView()
    view.apply(frame({"Status": "1"}, topic="TrackStatus", initial=True))
    view.apply(frame({"Lines": {"1": {}}}, initial=True))
    view.apply(frame({"Lines": {"1": {"Position": "2"}}}))

    snapshot = view.snapshot()

    assert list(snapshot["topics"]) == ["TimingData", "TrackStatus"]
    assert snapshot["topics"]["TimingData"]["updates"] == 1
    assert snapshot["topics"]["TimingData"]["snapshots"] == 1
    assert snapshot["applied_frames"] == 3


def test_rebuild_from_log_replays_snapshot_then_deltas(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        log.append(
            frame({"Lines": {"1": {"Position": "1"}, "2": {"Position": "2"}}}, initial=True)
        )
        log.append(frame({"Lines": {"1": {"Position": "9"}}}))

    view = rebuild_from_log(path)

    assert view.topics["TimingData"].payload["Lines"] == {
        "1": {"Position": "9"},
        "2": {"Position": "2"},
    }


def test_rebuild_from_a_missing_log_yields_an_empty_view(tmp_path: Path) -> None:
    view = rebuild_from_log(tmp_path / "absent.jsonl")

    assert view.applied_frames == 0
    assert view.snapshot()["topics"] == {}
