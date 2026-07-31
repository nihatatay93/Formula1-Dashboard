import json
from datetime import date
from pathlib import Path

import pytest

from app.live.collector import CollectorState, LiveCollector, LiveSessionIdentity
from app.live.policy import LiveTimingSettings
from app.live.replay_feed import (
    MAX_SCALED_DELAY_SECONDS,
    ReplayFeed,
    ReplayFeedError,
    build_replay_feed_factory,
)
from app.live.signalr_feed import SignalRFeed
from app.live.state import (
    build_feed_factory,
    feed_requires_authentication,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_signalr_qualifying.jsonl"
IDENTITY = LiveSessionIdentity(
    session_date=date(2026, 7, 25),
    event_name="Hungarian Grand Prix",
    session_key="qualifying",
)


def recording(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "recording.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


async def drain(feed: ReplayFeed) -> list[object]:
    return [frame async for frame in feed.stream()]


@pytest.mark.asyncio
async def test_replays_records_in_order_with_their_flags(tmp_path: Path) -> None:
    path = recording(
        tmp_path,
        [
            {"topic": "TrackStatus", "payload": {"Status": "1"}, "timestamp": "", "initial": True},
            {
                "topic": "TrackStatus",
                "payload": {"Status": "2"},
                "timestamp": "2026-07-25T14:00:01Z",
                "initial": False,
            },
        ],
    )
    slept: list[float] = []

    frames = await drain(
        ReplayFeed(path, sleep=lambda s: _record(slept, s))
    )

    assert [frame.topic for frame in frames] == ["TrackStatus", "TrackStatus"]
    assert frames[0].initial is True
    assert frames[1].initial is False
    assert frames[1].timestamp == "2026-07-25T14:00:01Z"


@pytest.mark.asyncio
async def test_initial_frames_are_not_paced(tmp_path: Path) -> None:
    path = recording(
        tmp_path,
        [
            {"topic": "SessionInfo", "payload": {"a": 1}, "timestamp": "", "initial": True},
            {"topic": "TrackStatus", "payload": {"b": 1}, "timestamp": "", "initial": True},
        ],
    )
    slept: list[float] = []

    await drain(ReplayFeed(path, sleep=lambda s: _record(slept, s)))

    assert slept == []


@pytest.mark.asyncio
async def test_pacing_divides_the_gap_by_the_speed(tmp_path: Path) -> None:
    path = recording(
        tmp_path,
        [
            {
                "topic": "TrackStatus",
                "payload": {"n": 1},
                "timestamp": "2026-07-25T14:00:00Z",
                "initial": False,
            },
            {
                "topic": "TrackStatus",
                "payload": {"n": 2},
                "timestamp": "2026-07-25T14:00:04Z",
                "initial": False,
            },
        ],
    )
    slept: list[float] = []

    await drain(
        ReplayFeed(path, speed=4.0, sleep=lambda s: _record(slept, s))
    )

    # The first timestamped frame has nothing to pace against.
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_a_long_gap_is_capped_so_replay_never_stalls(tmp_path: Path) -> None:
    path = recording(
        tmp_path,
        [
            {
                "topic": "TrackStatus",
                "payload": {"n": 1},
                "timestamp": "2026-07-25T14:00:00Z",
                "initial": False,
            },
            {
                "topic": "TrackStatus",
                "payload": {"n": 2},
                "timestamp": "2026-07-25T14:20:00Z",
                "initial": False,
            },
        ],
    )
    slept: list[float] = []

    await drain(
        ReplayFeed(path, speed=1.0, sleep=lambda s: _record(slept, s))
    )

    assert slept == [MAX_SCALED_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_malformed_lines_are_skipped_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl"
    path.write_text(
        '{"topic":"TrackStatus","payload":{"Status":"1"},"timestamp":"","initial":true}\n'
        "not json\n"
        "\n"
        "[1,2,3]\n"
        '{"topic":"TrackStatus","payload":{"Status":"2"},"timestamp":"","initial":true}\n',
        encoding="utf-8",
    )
    feed = ReplayFeed(path)

    frames = await drain(feed)

    assert len(frames) == 2
    assert feed.lines_skipped == 2
    assert feed.frames_emitted == 2


@pytest.mark.asyncio
async def test_an_unparseable_timestamp_does_not_stop_the_replay(
    tmp_path: Path,
) -> None:
    path = recording(
        tmp_path,
        [
            {
                "topic": "TrackStatus",
                "payload": {"n": 1},
                "timestamp": "nonsense",
                "initial": False,
            },
        ],
    )

    frames = await drain(ReplayFeed(path))

    assert len(frames) == 1


@pytest.mark.asyncio
async def test_a_missing_recording_raises_on_stream(tmp_path: Path) -> None:
    feed = ReplayFeed(tmp_path / "absent.jsonl")

    with pytest.raises(ReplayFeedError, match="not found"):
        await drain(feed)


def test_non_positive_speed_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReplayFeedError, match="speed"):
        ReplayFeed(tmp_path / "recording.jsonl", speed=0)


def test_factory_rejects_a_missing_recording(tmp_path: Path) -> None:
    with pytest.raises(ReplayFeedError, match="not found"):
        build_replay_feed_factory(tmp_path / "absent.jsonl")


def test_factory_returns_a_fresh_feed_per_connection() -> None:
    factory = build_replay_feed_factory(FIXTURE)

    assert factory() is not factory()


class TestFeedResolution:
    def test_without_a_recording_the_live_feed_is_used(self) -> None:
        settings = LiveTimingSettings()

        assert isinstance(build_feed_factory(settings)(), SignalRFeed)
        assert feed_requires_authentication(settings) is True

    def test_a_configured_recording_wins_and_needs_no_token(self) -> None:
        settings = LiveTimingSettings(replay_path=str(FIXTURE), replay_speed=50.0)

        # Replay is an explicit development choice; a stored token must not
        # silently override it.
        assert isinstance(build_feed_factory(settings)(), ReplayFeed)
        assert feed_requires_authentication(settings) is False

    def test_an_unusable_recording_falls_back_to_the_live_feed(
        self,
        tmp_path: Path,
    ) -> None:
        settings = LiveTimingSettings(replay_path=str(tmp_path / "absent.jsonl"))

        # A misconfigured recording must not stop the API from starting.
        assert isinstance(build_feed_factory(settings)(), SignalRFeed)


@pytest.mark.asyncio
async def test_replaying_the_recorded_fixture_builds_real_merged_state(
    tmp_path: Path,
) -> None:
    """End-to-end: a real recording drives the collector, log and view."""

    async def never_sleeps(_seconds: float) -> None:
        raise AssertionError("a finite feed must not schedule a reconnect delay")

    collector = LiveCollector(
        identity=IDENTITY,
        # A very high speed collapses pacing so the suite stays fast; pacing
        # itself is asserted separately above.
        feed_factory=build_replay_feed_factory(FIXTURE, speed=1_000_000.0),
        settings=LiveTimingSettings(log_directory=str(tmp_path)),
        log_directory=tmp_path,
        jitter=lambda: 0.0,
        sleep=never_sleeps,
    )

    # The recording ends the session on its own; nothing has to stop it.
    await collector.run()

    assert collector.finished is True
    assert collector.state is CollectorState.FINISHED
    assert collector.stats.reconnects == 0

    view = collector.view
    assert view.topics["SessionInfo"].payload["Type"] == "Qualifying"
    assert (
        view.topics["SessionInfo"].payload["Meeting"]["Name"]
        == "Hungarian Grand Prix"
    )
    lines = view.topics["TimingData"].payload["Lines"]
    assert len(lines) == 22, "every driver survived the deltas"
    assert view.topics["TimingData"].updates > 0
    # The deliberately dropped topics never reach the view.
    assert "CarData.z" not in view.topics
    assert "Position.z" not in view.topics
    assert collector.stats.rejected.get("ignored_topic", 0) > 0
    assert collector.log_path.exists()


def _record(sink: list[float], seconds: float):
    async def noop() -> None:
        return None

    sink.append(seconds)
    return noop()
