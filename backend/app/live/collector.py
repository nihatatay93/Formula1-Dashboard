"""On-demand SignalR live-timing collector.

The upstream feed is reached only through the ``LiveFeed`` protocol, so the
automated suite runs against controlled doubles and never opens a live
connection.

The collector owns one session: it normalizes untrusted frames, applies them to
an in-memory current view, appends accepted frames to a disposable JSONL log, and
publishes accepted frames to subscribers. Nothing here touches PostgreSQL.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.live.current_view import LiveCurrentView, rebuild_from_log
from app.live.frames import (
    CONSUMED_TOPICS,
    FrameRejection,
    LiveFrame,
    LiveFrameRejectedError,
    normalize_frame,
)
from app.live.policy import LiveTimingSettings, calculate_reconnect_delay
from app.live.session_log import LiveSessionLog, build_log_path


class CollectorState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class RawFrame:
    """One frame as received from the feed, before validation.

    Mirrors the confirmed wire shape ``{topic, payload, timestamp, initial}``.
    There is no sequence number: a connect delivers ``initial`` full state per
    topic and every later frame is a deep partial delta.
    """

    topic: str
    payload: object
    initial: bool = False
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class LiveSessionIdentity:
    session_date: date
    event_name: str
    session_key: str


class LiveFeed(Protocol):
    """One attempt at a live upstream connection."""

    def stream(self) -> AsyncIterator[RawFrame]:
        """Yield frames until the connection ends or raises."""
        ...

    async def close(self) -> None: ...


#: A factory is used rather than a single feed so each reconnect gets a fresh
#: connection, and so the collector never has to reset upstream state itself.
LiveFeedFactory = Callable[[], LiveFeed]


@dataclass
class CollectorStats:
    accepted: int = 0
    duplicates: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    connection_attempts: int = 0
    reconnects: int = 0
    dropped_by_log_cap: int = 0

    def record_rejection(self, reason: FrameRejection) -> None:
        self.rejected[reason.value] = self.rejected.get(reason.value, 0) + 1

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": dict(sorted(self.rejected.items())),
            "connection_attempts": self.connection_attempts,
            "reconnects": self.reconnects,
            "dropped_by_log_cap": self.dropped_by_log_cap,
        }


class LiveCollector:
    """Collects one live session into a current view and a disposable log."""

    def __init__(
        self,
        *,
        identity: LiveSessionIdentity,
        feed_factory: LiveFeedFactory,
        settings: LiveTimingSettings,
        log_directory: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        jitter: Callable[[], float] | None = None,
        sleep: Callable[[float], object] | None = None,
        logging_enabled: bool = True,
    ) -> None:
        self._logging_enabled = logging_enabled
        self._identity = identity
        self._feed_factory = feed_factory
        self._settings = settings
        self._clock = clock if clock is not None else lambda: datetime.now(tz=UTC)
        self._jitter = jitter if jitter is not None else random.random
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._directory = (
            Path(settings.log_directory) if log_directory is None else log_directory
        )
        self._log_path = build_log_path(
            self._directory,
            session_date=identity.session_date,
            event_name=identity.event_name,
            session_key=identity.session_key,
        )
        self._state = CollectorState.DISCONNECTED
        self._stats = CollectorStats()
        self._stopping = False
        self._log: LiveSessionLog | None = None
        # A restart mid-session rebuilds from the log rather than waiting for
        # the feed to resend everything.
        self._view = rebuild_from_log(self._log_path)
        self._subscribers: set[asyncio.Queue[Mapping[str, object]]] = set()

    @property
    def identity(self) -> LiveSessionIdentity:
        return self._identity

    @property
    def state(self) -> CollectorState:
        return self._state

    @property
    def stats(self) -> CollectorStats:
        return self._stats

    @property
    def view(self) -> LiveCurrentView:
        return self._view

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def log_degraded(self) -> bool:
        """True when frames are not reaching the log, for any reason."""
        if not self._logging_enabled:
            return True
        return self._log is not None and self._log.degraded

    def status(self) -> dict[str, object]:
        return {
            "state": self._state.value,
            "session": {
                "session_date": self._identity.session_date.isoformat(),
                "event_name": self._identity.event_name,
                "session_key": self._identity.session_key,
            },
            "topics_subscribed": sorted(CONSUMED_TOPICS),
            "log_degraded": self.log_degraded,
            "subscribers": len(self._subscribers),
            "stats": self._stats.as_dict(),
        }

    def subscribe(self, *, max_queue: int = 64) -> asyncio.Queue[Mapping[str, object]]:
        queue: asyncio.Queue[Mapping[str, object]] = asyncio.Queue(maxsize=max_queue)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Mapping[str, object]]) -> None:
        self._subscribers.discard(queue)

    async def run(self) -> None:
        """Stream until ``stop`` is requested, reconnecting with backoff."""
        if self._logging_enabled:
            self._log = LiveSessionLog(
                self._log_path,
                max_bytes=self._settings.max_log_bytes,
            )
        attempt = 0
        try:
            while not self._stopping:
                self._state = CollectorState.CONNECTING
                self._stats.connection_attempts += 1
                if self._stats.connection_attempts > 1:
                    self._stats.reconnects += 1
                attempt += 1
                feed = self._feed_factory()
                try:
                    await self._consume(feed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Any upstream failure is a reconnect, never a session
                    # failure, and never touches the archive retry budget.
                    pass
                finally:
                    await _close_quietly(feed)

                if self._stopping:
                    break
                # A clean stream end is still a reconnect; the feed decides when
                # a session is really over.
                delay = calculate_reconnect_delay(
                    attempt=attempt,
                    jitter_fraction=self._jitter(),
                    settings=self._settings,
                )
                self._state = CollectorState.DISCONNECTED
                await self._sleep(delay.total_seconds())
        finally:
            self._state = CollectorState.STOPPED
            if self._log is not None:
                self._log.close()

    async def _consume(self, feed: LiveFeed) -> None:
        self._state = CollectorState.STREAMING
        async for raw in feed.stream():
            if self._stopping:
                return
            self._handle(raw)

    def _handle(self, raw: RawFrame) -> None:
        try:
            frame = normalize_frame(
                getattr(raw, "topic", None),
                getattr(raw, "payload", None),
                received_at=self._clock(),
                initial=bool(getattr(raw, "initial", False)),
                feed_timestamp=getattr(raw, "timestamp", None),
            )
        except LiveFrameRejectedError as rejected:
            self._stats.record_rejection(rejected.reason)
            return

        # A merge that changes nothing is a replay, which a reconnect makes
        # routine. It is counted rather than logged again.
        if not self._view.apply(frame):
            self._stats.duplicates += 1
            return

        self._stats.accepted += 1
        if self._log is None or not self._log.append(frame):
            # Streaming continues regardless: losing the disposable log is
            # always preferable to interrupting the live view.
            self._stats.dropped_by_log_cap += 1
        self._publish(frame)

    def _publish(self, frame: LiveFrame) -> None:
        # Subscribers receive the merged topic state rather than the raw delta,
        # so a client that joined mid-session never has to reconstruct it.
        state = self._view.topics.get(frame.topic)
        update = {
            "type": "update",
            "topic": frame.topic,
            "initial": frame.initial,
            "received_at": frame.received_at.isoformat(),
            "payload": {} if state is None else state.payload,
        }
        for queue in tuple(self._subscribers):
            _offer(queue, update)

    def request_stop(self) -> None:
        self._stopping = True


def _offer(
    queue: asyncio.Queue[Mapping[str, object]],
    update: Mapping[str, object],
) -> None:
    """Enqueue an update, dropping the oldest entry for a slow subscriber.

    A slow client must not grow memory without bound or block collection, and a
    stale live frame has no value, so the oldest is discarded first.
    """
    while True:
        try:
            queue.put_nowait(update)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


async def _close_quietly(feed: LiveFeed) -> None:
    try:
        await feed.close()
    except Exception:
        pass
