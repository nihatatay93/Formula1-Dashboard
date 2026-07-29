"""In-memory latest-state view of one live session.

This is what connecting clients read, so a client receives an immediate snapshot
without replaying the session log. It also carries frame-level deduplication:
the feed replays, and a reconnect legitimately re-sends content, so a frame at
or below the last applied sequence for its topic is discarded.

The view is rebuilt from the session log when the collector restarts mid-session.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.live.frames import LiveFrame
from app.live.session_log import iter_frames


@dataclass(frozen=True, slots=True)
class TopicState:
    sequence: int
    received_at: datetime
    payload: Mapping[str, object]


class LiveCurrentView:
    """Latest accepted state per topic for one live session."""

    def __init__(self) -> None:
        self._topics: dict[str, TopicState] = {}
        self._applied = 0
        self._discarded = 0

    @property
    def applied_frames(self) -> int:
        return self._applied

    @property
    def discarded_frames(self) -> int:
        """Frames dropped as replays of an already-applied sequence."""
        return self._discarded

    @property
    def topics(self) -> Mapping[str, TopicState]:
        return dict(self._topics)

    def last_sequence(self, topic: str) -> int | None:
        state = self._topics.get(topic)
        return None if state is None else state.sequence

    def apply(self, frame: LiveFrame) -> bool:
        """Apply one frame. Returns False when it is a replay of a seen sequence."""
        existing = self._topics.get(frame.topic)
        if existing is not None and frame.sequence <= existing.sequence:
            self._discarded += 1
            return False
        self._topics[frame.topic] = TopicState(
            sequence=frame.sequence,
            received_at=frame.received_at,
            payload=frame.payload,
        )
        self._applied += 1
        return True

    def apply_all(self, frames: Iterable[LiveFrame]) -> int:
        return sum(1 for frame in frames if self.apply(frame))

    def latest_received_at(self) -> datetime | None:
        if not self._topics:
            return None
        return max(state.received_at for state in self._topics.values())

    def snapshot(self) -> dict[str, object]:
        """A JSON-safe snapshot for a connecting client."""
        latest = self.latest_received_at()
        return {
            "latest_received_at": None if latest is None else latest.isoformat(),
            "applied_frames": self._applied,
            "topics": {
                topic: {
                    "sequence": state.sequence,
                    "received_at": state.received_at.isoformat(),
                    "payload": state.payload,
                }
                for topic, state in sorted(self._topics.items())
            },
        }


def rebuild_from_log(path: Path) -> LiveCurrentView:
    """Rebuild a current view by replaying a session log from disk."""
    view = LiveCurrentView()
    view.apply_all(iter_frames(path))
    return view
