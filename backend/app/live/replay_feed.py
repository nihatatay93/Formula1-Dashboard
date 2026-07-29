"""A ``LiveFeed`` that replays a recorded SignalR session from disk.

This makes the whole live path exercisable without a session weekend: the
collector, merge semantics, session log, retention, WebSocket fan-out and
dashboard all run against real recorded frames.

It is not a substitute for the live client. It reads the same
``{topic, payload, timestamp, initial}`` records the feed emits, so a recording
made by any capture tool can drive the pipeline.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
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

    def __init__(
        self,
        path: Path,
        *,
        speed: float = 10.0,
        hold_open: bool = True,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        if speed <= 0:
            raise ReplayFeedError("speed must be positive")
        self._path = path
        self._speed = speed
        self._hold_open = hold_open
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
            timestamp = _parse_timestamp(record.get("timestamp"))
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
                timestamp=record.get("timestamp"),
            )

        if self._hold_open:
            # A recording running out is not a disconnect. Holding the
            # connection keeps the final state visible instead of making the
            # collector reconnect and replay the whole file from the start.
            await asyncio.Event().wait()

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
    hold_open: bool = True,
) -> Callable[[], ReplayFeed]:
    """A feed factory the collector can call once per connection attempt."""
    if not path.is_file():
        raise ReplayFeedError(f"replay recording not found: {path}")

    def factory() -> ReplayFeed:
        return ReplayFeed(path, speed=speed, hold_open=hold_open)

    return factory
