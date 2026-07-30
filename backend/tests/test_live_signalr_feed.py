"""Tests for the live SignalR feed.

The upstream client is replaced with a double through the ``builder`` seam, so
these never open a connection. What matters here is the translation between the
client's two message shapes and ``RawFrame``, and that the thread-to-loop bridge
terminates cleanly.
"""

import asyncio
import threading
from typing import Any

import pytest
from signalrcore.messages.completion_message import CompletionMessage

from app.live.collector import RawFrame
from app.live.frames import CONSUMED_TOPICS
from app.live.signalr_feed import (
    QUEUE_SIZE,
    SignalRFeed,
    SignalRUnauthenticatedError,
    build_signalr_feed_factory,
)

TOKEN = "abc123def456ghi789jkl012"


class FakeConnection:
    """Stands in for signalrcore's hub connection."""

    def __init__(self, *, fail_start: Exception | None = None) -> None:
        self._on_open: Any = None
        self._on_close: Any = None
        self._handlers: dict[str, Any] = {}
        self.started = False
        self.stopped = False
        self.subscribed: list[Any] = []
        self._fail_start = fail_start
        self.stop_blocks = threading.Event()
        self.block_stop = False

    def on_open(self, callback: Any) -> None:
        self._on_open = callback

    def on_close(self, callback: Any) -> None:
        self._on_close = callback

    def on(self, event: str, callback: Any) -> None:
        self._handlers[event] = callback

    def start(self) -> None:
        if self._fail_start is not None:
            raise self._fail_start
        self.started = True
        self._on_open()

    def send(self, target: str, arguments: Any, on_invocation: Any = None) -> None:
        self.subscribed.append((target, arguments))
        self._invocation = on_invocation

    def stop(self) -> None:
        if self.block_stop:
            self.stop_blocks.wait(timeout=30)
        self.stopped = True

    # Helpers a test uses to drive the feed.
    def deliver(self, message: Any) -> None:
        self._handlers["feed"](message)

    def complete(self, result: Any) -> None:
        self._invocation(completion(result))

    def drop(self) -> None:
        self._on_close()


def completion(result: Any) -> CompletionMessage:
    message = CompletionMessage.__new__(CompletionMessage)
    message.result = result
    message.error = None
    return message


def build_feed(
    connection: FakeConnection,
    *,
    token: str | None = TOKEN,
    topics: list[str] | None = None,
) -> SignalRFeed:
    return SignalRFeed(
        token_provider=lambda: token,
        topics=topics,
        builder=lambda: connection,
    )


@pytest.fixture(autouse=True)
def no_negotiate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-negotiate is a real HTTP call; never make it in tests."""

    class Response:
        cookies: dict[str, str] = {}

    monkeypatch.setattr(
        "app.live.signalr_feed.requests.options",
        lambda *args, **kwargs: Response(),
    )


async def collect(feed: SignalRFeed, connection: FakeConnection, drive: Any) -> list:
    """Run the stream, drive the connection, and return the frames yielded."""
    frames: list = []

    async def consume() -> None:
        async for frame in feed.stream():
            frames.append(frame)

    task = asyncio.create_task(consume())
    for _ in range(200):
        if connection.started:
            break
        await asyncio.sleep(0.01)
    await asyncio.to_thread(drive, connection)
    await asyncio.wait_for(task, timeout=5)
    return frames


class TestSubscription:
    @pytest.mark.asyncio
    async def test_it_subscribes_to_the_consumed_topics(self) -> None:
        connection = FakeConnection()

        await collect(build_feed(connection), connection, lambda c: c.drop())

        target, arguments = connection.subscribed[0]
        assert target == "Subscribe"
        assert arguments == [sorted(CONSUMED_TOPICS)]

    @pytest.mark.asyncio
    async def test_explicit_topics_override_the_default(self) -> None:
        connection = FakeConnection()

        await collect(
            build_feed(connection, topics=["TimingData"]),
            connection,
            lambda c: c.drop(),
        )

        assert connection.subscribed[0][1] == [["TimingData"]]

    def test_the_default_topic_list_excludes_the_compressed_topics(self) -> None:
        feed = build_feed(FakeConnection())

        assert "CarData.z" not in feed.topics
        assert "Position.z" not in feed.topics
        assert "TimingData" in feed.topics


class TestMessageTranslation:
    @pytest.mark.asyncio
    async def test_the_subscribe_completion_becomes_initial_frames(self) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (
                c.complete(
                    {
                        "TimingData": {"Lines": {"1": {"Position": "1"}}},
                        "TrackStatus": {"Status": "1"},
                    }
                ),
                c.drop(),
            ),
        )

        assert [f.topic for f in frames] == ["TimingData", "TrackStatus"]
        assert all(f.initial for f in frames)
        # Full state carries no feed timestamp, matching the recorded format.
        assert all(f.timestamp is None for f in frames)
        assert frames[0].payload == {"Lines": {"1": {"Position": "1"}}}

    @pytest.mark.asyncio
    async def test_a_feed_invocation_becomes_a_delta_frame(self) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (
                c.deliver(
                    ["TimingData", {"Lines": {"4": {}}}, "2026-07-25T14:43:27.786Z"]
                ),
                c.drop(),
            ),
        )

        assert len(frames) == 1
        frame = frames[0]
        assert isinstance(frame, RawFrame)
        assert frame.topic == "TimingData"
        assert frame.initial is False
        assert frame.timestamp == "2026-07-25T14:43:27.786Z"

    @pytest.mark.asyncio
    async def test_a_delta_without_a_timestamp_is_still_accepted(self) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (c.deliver(["TrackStatus", {"Status": "2"}]), c.drop()),
        )

        assert frames[0].timestamp is None

    @pytest.mark.asyncio
    async def test_a_non_string_timestamp_is_discarded_not_forwarded(self) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (c.deliver(["TrackStatus", {}, 12345]), c.drop()),
        )

        assert frames[0].timestamp is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message",
        [None, "text", 7, [], ["only-topic"], {"not": "a list"}],
    )
    async def test_unrecognised_shapes_are_ignored(self, message: Any) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (c.deliver(message), c.drop()),
        )

        assert frames == []

    @pytest.mark.asyncio
    async def test_a_completion_with_a_non_mapping_result_is_ignored(self) -> None:
        connection = FakeConnection()

        frames = await collect(
            build_feed(connection),
            connection,
            lambda c: (c.complete(["not", "a", "mapping"]), c.drop()),
        )

        assert frames == []


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_the_stream_ends_when_the_connection_closes(self) -> None:
        connection = FakeConnection()

        frames = await collect(build_feed(connection), connection, lambda c: c.drop())

        # Ending rather than hanging is what lets the collector reconnect.
        assert frames == []

    @pytest.mark.asyncio
    async def test_without_a_token_it_refuses_to_connect(self) -> None:
        connection = FakeConnection()
        feed = build_feed(connection, token=None)

        with pytest.raises(SignalRUnauthenticatedError):
            async for _ in feed.stream():
                pass

        assert connection.started is False

    @pytest.mark.asyncio
    async def test_an_expired_token_is_treated_as_absent(self) -> None:
        connection = FakeConnection()
        feed = build_feed(connection, token="")

        with pytest.raises(SignalRUnauthenticatedError):
            async for _ in feed.stream():
                pass

    @pytest.mark.asyncio
    async def test_close_stops_the_connection(self) -> None:
        connection = FakeConnection()
        feed = build_feed(connection)
        task = asyncio.create_task(_drain(feed))
        for _ in range(200):
            if connection.started:
                break
            await asyncio.sleep(0.01)

        await feed.close()
        await asyncio.wait_for(task, timeout=5)

        assert connection.stopped is True

    @pytest.mark.asyncio
    async def test_a_stop_that_never_returns_cannot_hang_shutdown(self) -> None:
        connection = FakeConnection()
        connection.block_stop = True
        feed = build_feed(connection)
        task = asyncio.create_task(_drain(feed))
        for _ in range(200):
            if connection.started:
                break
            await asyncio.sleep(0.01)

        # Bounded by STOP_TIMEOUT_SECONDS, so this returns rather than blocking.
        await asyncio.wait_for(feed.close(), timeout=10)
        connection.stop_blocks.set()
        await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_a_flood_never_blocks_the_connection_thread(self) -> None:
        connection = FakeConnection()
        feed = build_feed(connection)
        frames: list = []

        async def consume() -> None:
            async for frame in feed.stream():
                frames.append(frame)

        task = asyncio.create_task(consume())
        for _ in range(200):
            if connection.started:
                break
            await asyncio.sleep(0.01)

        def flood(conn: FakeConnection) -> None:
            for index in range(QUEUE_SIZE + 50):
                conn.deliver(["TrackStatus", {"n": index}])
            conn.drop()

        # Completing at all is the assertion: publishing schedules onto the loop
        # rather than blocking, so the connection thread is never held up.
        await asyncio.wait_for(asyncio.to_thread(flood, connection), timeout=15)
        await asyncio.wait_for(task, timeout=15)

        # A consumer that keeps up receives everything; the loop's callback
        # queue absorbs the burst rather than the bounded queue overflowing.
        assert len(frames) == QUEUE_SIZE + 50

    def test_offer_discards_when_the_queue_is_genuinely_full(self) -> None:
        """The guard for a stalled consumer, exercised directly.

        The flood above never reaches this path because the loop drains faster
        than the queue fills, so the drop behaviour is asserted on the unit that
        implements it.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        queue.put_nowait("first")
        queue.put_nowait("second")

        SignalRFeed._offer(queue, "third")

        assert queue.qsize() == 2
        assert [queue.get_nowait(), queue.get_nowait()] == ["first", "second"]


class TestFactory:
    def test_it_returns_a_fresh_feed_per_connection(self) -> None:
        factory = build_signalr_feed_factory(lambda: TOKEN)

        assert factory() is not factory()

    def test_the_token_provider_is_consulted_per_feed(self) -> None:
        calls: list[int] = []

        def provider() -> str:
            calls.append(1)
            return TOKEN

        feed = build_signalr_feed_factory(provider)()
        # Nothing is read until a connection is actually attempted.
        assert calls == []
        assert feed.topics


async def _drain(feed: SignalRFeed) -> None:
    async for _ in feed.stream():
        pass
