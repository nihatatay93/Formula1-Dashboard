"""A ``LiveFeed`` that replays a recorded SignalR session from disk.

This makes the whole live path exercisable without a session weekend: the
collector, merge semantics, session log, retention, WebSocket fan-out and
dashboard all run against real recorded frames.

It is not a substitute for the live client. Two record shapes are accepted,
because the two recordings that exist do not agree:

* a capture-tool file writes ``{topic, payload, timestamp, initial}``;
* this application's own session log writes
  ``{received_at, topic, initial, feed_timestamp, payload}``.

Reading only the first shape would silently replay a session log with no pacing
at all — ``timestamp`` is absent, so every frame would be emitted at once — so
the feed timestamp is resolved from either spelling, falling back to the log's
``received_at``.

The feed is ``finite``: a recording that runs out is the end of the session, not
a dropped connection, and the collector uses that to stop cleanly instead of
reconnecting and replaying the file from the top.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path

from app.live.collector import RawFrame

#: A long gap in a recording should not stall a replay, so scaled delays are
#: capped. Real sessions contain minutes of inactivity between runs.
MAX_SCALED_DELAY_SECONDS = 5.0


class ReplayFeedError(ValueError):
    """Raised when a replay recording cannot be used."""


class ReplayFeed:
    """Replays one recording, optionally faster than real time.

    ``initial`` frames are emitted immediately, matching a real connect. Later
    frames are paced by the difference between their feed timestamps divided by
    ``speed``.
    """

    #: A recording has an end, unlike a live connection. The collector reads
    #: this to treat a finished stream as the end of the session.
    finite = True

    def __init__(
        self,
        path: Path,
        *,
        speed: float = 10.0,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        if speed <= 0:
            raise ReplayFeedError("speed must be positive")
        self._path = path
        self._speed = speed
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._frames_emitted = 0
        self._lines_skipped = 0

    @property
    def frames_emitted(self) -> int:
        return self._frames_emitted

    @property
    def lines_skipped(self) -> int:
        return self._lines_skipped

    async def stream(self) -> AsyncIterator[RawFrame]:
        if not self._path.is_file():
            raise ReplayFeedError(f"replay recording not found: {self._path}")

        previous: datetime | None = None
        for record in self._records():
            timestamp = _parse_timestamp(_pacing_value(record))
            if timestamp is not None and previous is not None:
                delay = (timestamp - previous).total_seconds() / self._speed
                if delay > 0:
                    await self._sleep(min(delay, MAX_SCALED_DELAY_SECONDS))
            if timestamp is not None:
                previous = timestamp

            self._frames_emitted += 1
            yield RawFrame(
                topic=record.get("topic"),
                payload=record.get("payload"),
                initial=bool(record.get("initial")),
                timestamp=_feed_timestamp(record),
            )

    def _records(self) -> Iterator[dict[str, object]]:
        # Line-at-a-time synchronous reads: a local recording is small enough
        # that this never meaningfully blocks the event loop.
        with self._path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    decoded = json.loads(stripped)
                except ValueError:
                    self._lines_skipped += 1
                    continue
                if not isinstance(decoded, dict):
                    self._lines_skipped += 1
                    continue
                yield decoded

    async def close(self) -> None:
        return None


def _feed_timestamp(record: Mapping[str, object]) -> object:
    """The feed's own instant, under either recording's spelling."""
    value = record.get("timestamp")
    if isinstance(value, str) and value.strip():
        return value
    return record.get("feed_timestamp")


def _pacing_value(record: Mapping[str, object]) -> object:
    """What to pace by, preferring the feed's instant over the log's.

    A session log's ``initial`` frames carry a null ``feed_timestamp`` — the
    feed sends those with an empty timestamp — so ``received_at`` is the only
    ordering a log offers for them.
    """
    value = _feed_timestamp(record)
    if isinstance(value, str) and value.strip():
        return value
    return record.get("received_at")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_replay_feed_factory(
    path: Path,
    *,
    speed: float = 10.0,
) -> Callable[[], ReplayFeed]:
    """A feed factory the collector can call once per connection attempt."""
    if not path.is_file():
        raise ReplayFeedError(f"replay recording not found: {path}")

    def factory() -> ReplayFeed:
        return ReplayFeed(path, speed=speed)

    return factory
