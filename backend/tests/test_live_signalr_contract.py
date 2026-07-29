"""Contract tests over real recorded SignalR frames.

The fixture is a trimmed extract of a Hungarian Grand Prix 2026 qualifying
session: every ``initial`` full-state frame plus representative deltas, including
the deeply nested and index-keyed-array cases that a hand-written fixture would
not have revealed. It contains no credentials.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.current_view import LiveCurrentView, merge_delta
from app.live.frames import (
    CONSUMED_TOPICS,
    IGNORED_TOPICS,
    FrameRejection,
    LiveFrame,
    LiveFrameRejectedError,
    normalize_frame,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_signalr_qualifying.jsonl"
BASE_TIME = datetime(2026, 7, 25, 14, 25, 0, tzinfo=UTC)


def records() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def accepted_frames() -> Iterator[LiveFrame]:
    """Every fixture frame the pipeline accepts, in recorded order."""
    for index, record in enumerate(records()):
        try:
            yield normalize_frame(
                record.get("topic"),
                record.get("payload"),
                received_at=BASE_TIME + timedelta(seconds=index),
                initial=bool(record.get("initial")),
                feed_timestamp=record.get("timestamp"),
            )
        except LiveFrameRejectedError:
            continue


def record_for(topic: str, *, initial: bool) -> dict[str, object]:
    for record in records():
        if record["topic"] == topic and bool(record["initial"]) is initial:
            return record
    raise AssertionError(f"fixture has no initial={initial} frame for {topic}")


def test_fixture_covers_every_topic_the_feed_sent() -> None:
    topics = {record["topic"] for record in records()}

    assert topics <= CONSUMED_TOPICS | IGNORED_TOPICS
    assert "TimingData" in topics
    assert topics & IGNORED_TOPICS, "the ignored path must stay covered"


def test_fixture_contains_no_credentials() -> None:
    raw = FIXTURE.read_text(encoding="utf-8").casefold()

    for fragment in ("password", "secret", "authorization", "bearer", "apikey"):
        assert fragment not in raw


def test_compressed_telemetry_topics_are_ignored_not_unknown() -> None:
    for topic in ("CarData.z", "Position.z"):
        record = next(r for r in records() if r["topic"] == topic)
        with pytest.raises(LiveFrameRejectedError) as caught:
            normalize_frame(
                record["topic"],
                record["payload"],
                received_at=BASE_TIME,
                initial=bool(record["initial"]),
                feed_timestamp=record["timestamp"],
            )
        # Their payloads are base64 strings, but the topic decides first so the
        # counter distinguishes a deliberate drop from a surprising new topic.
        assert caught.value.reason is FrameRejection.IGNORED_TOPIC


def test_a_genuinely_new_topic_is_reported_as_unknown() -> None:
    with pytest.raises(LiveFrameRejectedError) as caught:
        normalize_frame(
            "SomeFutureTopic",
            {"x": 1},
            received_at=BASE_TIME,
            initial=False,
        )

    assert caught.value.reason is FrameRejection.UNKNOWN_TOPIC


def test_initial_frames_carry_no_feed_timestamp() -> None:
    record = record_for("TimingData", initial=True)

    frame = normalize_frame(
        record["topic"],
        record["payload"],
        received_at=BASE_TIME,
        initial=True,
        feed_timestamp=record["timestamp"],
    )

    assert record["timestamp"] == ""
    assert frame.feed_timestamp is None
    assert frame.initial is True


def test_delta_frames_keep_the_feeds_high_precision_timestamp() -> None:
    record = record_for("TimingData", initial=False)

    frame = normalize_frame(
        record["topic"],
        record["payload"],
        received_at=BASE_TIME,
        initial=False,
        feed_timestamp=record["timestamp"],
    )

    assert frame.feed_timestamp is not None
    assert frame.feed_timestamp.tzinfo is not None


def test_initial_timing_data_seeds_every_driver() -> None:
    record = record_for("TimingData", initial=True)
    view = LiveCurrentView()

    view.apply(
        normalize_frame(
            record["topic"],
            record["payload"],
            received_at=BASE_TIME,
            initial=True,
            feed_timestamp=record["timestamp"],
        )
    )

    lines = view.topics["TimingData"].payload["Lines"]
    assert isinstance(lines, dict)
    assert len(lines) == 22


def test_a_nested_delta_preserves_every_other_driver() -> None:
    """The regression the recording exposed: replacing state would lose 21 drivers."""
    initial = record_for("TimingData", initial=True)
    view = LiveCurrentView()
    view.apply(
        normalize_frame(
            initial["topic"],
            initial["payload"],
            received_at=BASE_TIME,
            initial=True,
            feed_timestamp=initial["timestamp"],
        )
    )
    before = view.topics["TimingData"].payload

    delta = next(
        r
        for r in records()
        if r["topic"] == "TimingData"
        and not r["initial"]
        and len(r["payload"].get("Lines", {})) == 1
        and "Segments" in json.dumps(r["payload"])
    )
    driver = next(iter(delta["payload"]["Lines"]))
    view.apply(
        normalize_frame(
            delta["topic"],
            delta["payload"],
            received_at=BASE_TIME + timedelta(seconds=1),
            initial=False,
            feed_timestamp=delta["timestamp"],
        )
    )
    after = view.topics["TimingData"].payload

    assert len(after["Lines"]) == 22
    assert set(after["Lines"]) == set(before["Lines"])
    # Untouched drivers are unchanged; the targeted driver is not.
    for number in after["Lines"]:
        if number != driver:
            assert after["Lines"][number] == before["Lines"][number]
    assert after["Lines"][driver] != before["Lines"][driver]
    # Sibling keys of the patched driver survive.
    assert after["Lines"][driver].keys() >= before["Lines"][driver].keys()


def test_index_keyed_patch_updates_a_list_in_place() -> None:
    """Sectors/BestLapTimes/Stats arrive as arrays, then as {"1": {...}} deltas."""
    target = {
        "BestLapTimes": [{"Value": "1:18.277", "Lap": 3}, {"Value": ""}, {}],
    }

    merged = merge_delta(target, {"BestLapTimes": {"1": {"Value": "1:18.249"}}})

    assert isinstance(merged, dict)
    best = merged["BestLapTimes"]
    assert isinstance(best, list), "an index-keyed patch must not turn a list into a dict"
    assert len(best) == 3
    assert best[0] == {"Value": "1:18.277", "Lap": 3}
    assert best[1] == {"Value": "1:18.249"}


def test_out_of_range_index_patch_is_dropped_not_grown() -> None:
    merged = merge_delta({"Stats": [{"a": 1}]}, {"Stats": {"9": {"a": 2}}})

    assert merged == {"Stats": [{"a": 1}]}


def test_replaying_the_whole_fixture_produces_consistent_state() -> None:
    view = LiveCurrentView()
    accepted = list(accepted_frames())

    applied = view.apply_all(accepted)

    assert applied > 0
    assert set(view.topics) <= CONSUMED_TOPICS
    assert not set(view.topics) & IGNORED_TOPICS
    session = view.topics["SessionInfo"].payload
    assert session["Type"] == "Qualifying"
    assert session["Meeting"]["Name"] == "Hungarian Grand Prix"
    assert view.topics["TimingData"].updates > 0


def test_replaying_the_fixture_twice_is_idempotent() -> None:
    accepted = list(accepted_frames())
    once = LiveCurrentView()
    once.apply_all(accepted)

    twice = LiveCurrentView()
    twice.apply_all(accepted)
    twice.apply_all(accepted)

    assert {t: s.payload for t, s in twice.topics.items()} == {
        t: s.payload for t, s in once.topics.items()
    }
    # The second pass is recognised as replay rather than treated as new data.
    assert twice.unchanged_frames > once.unchanged_frames


def test_a_reconnect_snapshot_replaces_rather_than_merges() -> None:
    initial = record_for("TimingData", initial=True)
    view = LiveCurrentView()
    frame = normalize_frame(
        initial["topic"],
        initial["payload"],
        received_at=BASE_TIME,
        initial=True,
        feed_timestamp=initial["timestamp"],
    )
    view.apply(frame)
    view.apply(
        normalize_frame(
            "TimingData",
            {"Lines": {"1": {"Position": "99"}}},
            received_at=BASE_TIME + timedelta(seconds=1),
            initial=False,
        )
    )
    assert view.topics["TimingData"].payload["Lines"]["1"]["Position"] == "99"

    view.apply(frame)

    state = view.topics["TimingData"]
    assert state.payload["Lines"]["1"]["Position"] != "99"
    assert state.snapshots == 2
    assert state.updates == 0


def test_every_accepted_frame_survives_a_log_round_trip() -> None:
    for frame in accepted_frames():
        assert LiveFrame.from_log_line(frame.to_log_line()) == frame
