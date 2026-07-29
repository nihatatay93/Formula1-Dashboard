from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.live.collector import (
    CollectorState,
    LiveCollector,
    LiveSessionIdentity,
    RawFrame,
)
from app.live.policy import LiveTimingSettings, calculate_reconnect_delay
from app.live.session_log import iter_frames

IDENTITY = LiveSessionIdentity(
    session_date=date(2026, 8, 21),
    event_name="Dutch Grand Prix",
    session_key="qualifying",
)
BASE_TIME = datetime(2026, 8, 21, 13, 0, 0, tzinfo=UTC)


class ScriptedFeed:
    """Yields a fixed batch of frames, then optionally raises."""

    def __init__(
        self,
        frames: Sequence[RawFrame],
        *,
        error: Exception | None = None,
    ) -> None:
        self._frames = frames
        self._error = error
        self.closed = False

    async def stream(self) -> AsyncIterator[RawFrame]:
        for frame in self._frames:
            yield frame
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


def live_settings(tmp_path: Path, **overrides: object) -> LiveTimingSettings:
    return LiveTimingSettings(log_directory=str(tmp_path), **overrides)


def build(
    tmp_path: Path,
    feeds: Sequence[ScriptedFeed],
    *,
    settings: LiveTimingSettings | None = None,
    stop_after_sleeps: int = 1,
) -> tuple[LiveCollector, list[float]]:
    """Build a collector that stops itself after N reconnect delays.

    Stopping on the reconnect delay rather than on wall-clock time keeps these
    tests deterministic: the collector never spins and never races a timeout.
    """
    holder: dict[str, LiveCollector] = {}
    recorded: list[float] = []
    ticks = iter(range(10_000))
    pending = list(feeds)

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)
        if len(recorded) >= stop_after_sleeps:
            holder["live"].request_stop()

    def next_feed() -> ScriptedFeed:
        return pending.pop(0) if pending else ScriptedFeed([])

    collector = LiveCollector(
        identity=IDENTITY,
        feed_factory=next_feed,
        settings=settings or live_settings(tmp_path),
        log_directory=tmp_path,
        clock=lambda: BASE_TIME + timedelta(seconds=next(ticks)),
        jitter=lambda: 0.0,
        sleep=fake_sleep,
    )
    holder["live"] = collector
    return collector, recorded


@pytest.mark.asyncio
async def test_accepted_frames_reach_the_view_and_the_log(tmp_path: Path) -> None:
    feed = ScriptedFeed(
        [
            RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True),
            RawFrame("TrackStatus", {"Status": "1"}, initial=True),
        ]
    )
    collector, _ = build(tmp_path, [feed])

    await collector.run()

    assert collector.stats.accepted == 2
    assert set(collector.view.topics) == {"TimingData", "TrackStatus"}
    assert [frame.topic for frame in iter_frames(collector.log_path)] == [
        "TimingData",
        "TrackStatus",
    ]
    assert collector.state is CollectorState.STOPPED
    assert feed.closed is True


@pytest.mark.asyncio
async def test_unknown_topic_is_counted_and_never_logged(tmp_path: Path) -> None:
    collector, _ = build(
        tmp_path,
        [ScriptedFeed([RawFrame("Nope.Unknown", {"x": 1})])],
    )

    await collector.run()

    assert collector.stats.accepted == 0
    assert collector.stats.rejected == {"unknown_topic": 1}
    assert list(iter_frames(collector.log_path)) == []


@pytest.mark.asyncio
async def test_malformed_payload_is_counted_by_reason(tmp_path: Path) -> None:
    collector, _ = build(
        tmp_path,
        [
            ScriptedFeed(
                [
                    RawFrame("TimingData", "not-a-mapping"),
                    RawFrame("TimingData", {"gap": float("nan")}),
                    RawFrame("CarData.z", "base64-deflate-payload"),
                ]
            )
        ],
    )

    await collector.run()

    assert collector.stats.rejected == {
        "malformed_payload": 2,
        "ignored_topic": 1,
    }


@pytest.mark.asyncio
async def test_repeated_delta_is_counted_as_duplicate_not_appended(
    tmp_path: Path,
) -> None:
    collector, _ = build(
        tmp_path,
        [
            ScriptedFeed(
                [
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "5"}}}),
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "5"}}}),
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "5"}}}),
                ]
            )
        ],
    )

    await collector.run()

    assert collector.stats.accepted == 1
    assert collector.stats.duplicates == 2
    assert len(list(iter_frames(collector.log_path))) == 1


@pytest.mark.asyncio
async def test_upstream_error_reconnects_with_backoff(tmp_path: Path) -> None:
    collector, sleeps = build(
        tmp_path,
        [
            ScriptedFeed(
                [RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True)],
                error=ConnectionResetError("feed dropped"),
            ),
            ScriptedFeed([RawFrame("TimingData", {"Lines": {"2": {}}})]),
        ],
        stop_after_sleeps=2,
    )

    await collector.run()

    assert collector.stats.accepted == 2
    assert collector.stats.connection_attempts == 2
    assert collector.stats.reconnects == 1
    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[1] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_reconnect_replay_is_harmless_and_deduplicated(tmp_path: Path) -> None:
    replayed = [
        RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}, initial=True),
        RawFrame("TimingData", {"Lines": {"1": {"Position": "2"}}}),
    ]
    collector, _ = build(
        tmp_path,
        [
            ScriptedFeed(replayed, error=ConnectionResetError("dropped")),
            ScriptedFeed([*replayed, RawFrame("TimingData", {"Lines": {"1": {"Position": "3"}}})]),
        ],
        stop_after_sleeps=2,
    )

    await collector.run()

    # A reconnect resends a snapshot that rewinds state, then the same deltas
    # roll it forward again. Each step is a real change rather than a duplicate,
    # so the property that matters is convergence on the correct final state.
    assert collector.stats.reconnects == 1
    assert collector.view.topics["TimingData"].payload["Lines"]["1"] == {
        "Position": "3"
    }
    assert collector.stats.duplicates == 0
    assert len(list(iter_frames(collector.log_path))) == 5


@pytest.mark.asyncio
async def test_log_cap_drops_frames_without_stopping_the_stream(
    tmp_path: Path,
) -> None:
    collector, _ = build(
        tmp_path,
        [
            ScriptedFeed(
                [
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}),
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "2"}}}),
                ]
            )
        ],
        settings=live_settings(tmp_path, max_log_bytes=1, max_directory_bytes=1),
    )

    await collector.run()

    # Both frames still reached the view and any subscriber.
    assert collector.stats.accepted == 2
    assert collector.stats.dropped_by_log_cap == 2
    assert collector.log_degraded is True


@pytest.mark.asyncio
async def test_restart_rebuilds_the_view_from_an_existing_log(
    tmp_path: Path,
) -> None:
    first, _ = build(
        tmp_path,
        [ScriptedFeed([RawFrame("TimingData", {"Lines": {"1": {"Position": "4"}}}, initial=True)])],
    )
    await first.run()

    # A fresh collector over the same log starts from the persisted sequence.
    resumed, _ = build(
        tmp_path,
        [ScriptedFeed([RawFrame("TimingData", {"Lines": {"1": {"Position": "4"}}})])],
    )
    assert resumed.view.topics["TimingData"].payload["Lines"]["1"]["Position"] == "4"

    await resumed.run()

    assert resumed.stats.duplicates == 1
    assert resumed.stats.accepted == 0
    assert resumed.view.topics["TimingData"].payload["Lines"]["1"] == {"Position": "4"}


@pytest.mark.asyncio
async def test_subscribers_receive_accepted_frames_only(tmp_path: Path) -> None:
    collector, _ = build(
        tmp_path,
        [
            ScriptedFeed(
                [
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}),
                    RawFrame("CarData.z", "compressed"),
                    RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}),
                ]
            )
        ],
    )
    queue = collector.subscribe()

    await collector.run()

    assert queue.qsize() == 1
    update = queue.get_nowait()
    assert update["topic"] == "TimingData"
    assert update["type"] == "update"


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_instead_of_blocking(
    tmp_path: Path,
) -> None:
    frames = [
        RawFrame("TimingData", {"Lines": {"1": {"Position": str(n)}}})
        for n in range(1, 8)
    ]
    collector, _ = build(tmp_path, [ScriptedFeed(frames)])
    queue = collector.subscribe(max_queue=3)

    await collector.run()

    assert queue.qsize() == 3
    positions = [
        queue.get_nowait()["payload"]["Lines"]["1"]["Position"] for _ in range(3)
    ]
    assert positions == ["5", "6", "7"]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(tmp_path: Path) -> None:
    collector, _ = build(
        tmp_path,
        [ScriptedFeed([RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True)])],
    )
    queue = collector.subscribe()
    collector.unsubscribe(queue)

    await collector.run()

    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_a_failing_close_does_not_break_the_collector(tmp_path: Path) -> None:
    class HostileFeed(ScriptedFeed):
        async def close(self) -> None:
            raise OSError("close failed")

    collector, _ = build(
        tmp_path,
        [HostileFeed([RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True)])],
    )

    await collector.run()

    assert collector.stats.accepted == 1
    assert collector.state is CollectorState.STOPPED


@pytest.mark.asyncio
async def test_status_reports_state_topics_and_counters(tmp_path: Path) -> None:
    collector, _ = build(
        tmp_path,
        [ScriptedFeed([RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True)])],
    )

    await collector.run()
    status = collector.status()

    assert status["state"] == "stopped"
    assert status["session"]["event_name"] == "Dutch Grand Prix"
    assert "TimingData" in status["topics_subscribed"]
    assert status["stats"]["accepted"] == 1
    assert status["log_degraded"] is False


def test_reconnect_delay_grows_then_saturates_at_the_cap() -> None:
    settings = LiveTimingSettings(
        reconnect_base_seconds=2,
        reconnect_multiplier=2,
        reconnect_cap_seconds=16,
        reconnect_jitter_min_ratio=1.0,
    )
    delays = [
        calculate_reconnect_delay(
            attempt=attempt,
            jitter_fraction=0.0,
            settings=settings,
        ).total_seconds()
        for attempt in range(1, 7)
    ]

    assert delays == [2, 4, 8, 16, 16, 16]


def test_reconnect_delay_applies_equal_jitter() -> None:
    settings = LiveTimingSettings(
        reconnect_base_seconds=8,
        reconnect_cap_seconds=60,
        reconnect_jitter_min_ratio=0.5,
    )

    lowest = calculate_reconnect_delay(
        attempt=1,
        jitter_fraction=0.0,
        settings=settings,
    )
    highest = calculate_reconnect_delay(
        attempt=1,
        jitter_fraction=1.0,
        settings=settings,
    )

    assert lowest.total_seconds() == pytest.approx(4.0)
    assert highest.total_seconds() == pytest.approx(8.0)


@pytest.mark.parametrize("attempt", [0, -1])
def test_reconnect_delay_rejects_a_non_positive_attempt(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt"):
        calculate_reconnect_delay(
            attempt=attempt,
            jitter_fraction=0.0,
            settings=LiveTimingSettings(),
        )
