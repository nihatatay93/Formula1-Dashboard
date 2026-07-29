"""In-memory merged state of one live session.

This is what connecting clients read, so a client receives an immediate snapshot
without replaying the session log.

The feed sends one ``initial`` frame per topic carrying full state, then deep
partial deltas. A delta must therefore be merged into the accumulated state, not
substituted for it: a single real frame is as small as

    {"Lines": {"14": {"Sectors": {"1": {"Segments": {"0": {"Status": 2051}}}}}}}

and replacing topic state with that would discard every other driver.

Arrays are patched as objects keyed by index. ``Sectors``, ``BestLapTimes`` and
``Stats`` arrive as JSON arrays in the initial frame and as ``{"1": {...}}`` in
deltas, so an index-keyed mapping applied to a list target updates in place.

Because merging the same delta twice yields the same state, replays after a
reconnect need no sequence tracking; a re-applied frame simply reports no change.
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
    received_at: datetime
    feed_timestamp: datetime | None
    payload: Mapping[str, object]
    #: Full-state frames seen, and deltas merged since the last of them.
    snapshots: int
    updates: int


def merge_delta(target: object, patch: object) -> object:
    """Apply a feed delta to accumulated state, returning new state.

    Neither argument is mutated. A mapping patch merges key by key; a mapping
    patch against a list target is treated as an index-keyed array patch; any
    other patch replaces the target outright.
    """
    if not isinstance(patch, Mapping):
        return patch

    if isinstance(target, Mapping):
        merged: dict[str, object] = dict(target)
        for key, value in patch.items():
            merged[str(key)] = merge_delta(merged.get(str(key)), value)
        return merged

    if isinstance(target, list):
        patched = list(target)
        for key, value in patch.items():
            index = _array_index(key)
            # Out-of-range indexes are dropped rather than extending the array,
            # so untrusted input cannot grow state without bound.
            if index is None or not 0 <= index < len(patched):
                continue
            patched[index] = merge_delta(patched[index], value)
        return patched

    return {str(key): merge_delta(None, value) for key, value in patch.items()}


def _array_index(key: object) -> int | None:
    if not isinstance(key, str) or not key.isdecimal():
        return None
    try:
        return int(key)
    except ValueError:
        return None


class LiveCurrentView:
    """Accumulated per-topic state for one live session."""

    def __init__(self) -> None:
        self._topics: dict[str, TopicState] = {}
        self._applied = 0
        self._unchanged = 0

    @property
    def applied_frames(self) -> int:
        return self._applied

    @property
    def unchanged_frames(self) -> int:
        """Frames whose merge produced no change, such as a reconnect replay."""
        return self._unchanged

    @property
    def topics(self) -> Mapping[str, TopicState]:
        return dict(self._topics)

    def apply(self, frame: LiveFrame) -> bool:
        """Merge one frame. Returns False when it changed nothing."""
        existing = self._topics.get(frame.topic)

        # An initial frame replaces topic state. A delta arriving before any
        # snapshot has nothing to merge into, so it seeds the state instead.
        if frame.initial or existing is None:
            payload = dict(frame.payload)
            previous_snapshots = existing.snapshots if existing is not None else 0
            unchanged = existing is not None and existing.payload == payload
            self._topics[frame.topic] = TopicState(
                received_at=frame.received_at,
                feed_timestamp=frame.feed_timestamp,
                payload=payload,
                snapshots=previous_snapshots + (1 if frame.initial else 0),
                updates=0,
            )
            if unchanged:
                self._unchanged += 1
                return False
            self._applied += 1
            return True

        merged = merge_delta(existing.payload, frame.payload)
        if not isinstance(merged, dict) or merged == existing.payload:
            self._unchanged += 1
            return False

        self._topics[frame.topic] = TopicState(
            received_at=frame.received_at,
            feed_timestamp=frame.feed_timestamp,
            payload=merged,
            snapshots=existing.snapshots,
            updates=existing.updates + 1,
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
                    "received_at": state.received_at.isoformat(),
                    "feed_timestamp": (
                        None
                        if state.feed_timestamp is None
                        else state.feed_timestamp.isoformat()
                    ),
                    "snapshots": state.snapshots,
                    "updates": state.updates,
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
