"""Tracks how far each driver has moved during a live session.

The feed carries no grid or starting position — checked against a recorded
session, where the only order fields are ``Position`` and a ``Line`` that never
once differed from it — so "places gained" can only be measured against the
first position this collector saw. That baseline is honest but conditional: it
means places gained since the session was connected, which equals the grid only
when collection began before the start. The board reports the baseline alongside
the movement so a reader is never left guessing which it is.

State lives here rather than in the merged topic view because the view holds
only what the feed last said, while movement is a fact about history. It lives
server-side rather than in the dashboard because the dashboard receives a
stateless board and would lose its baseline on every reconnect.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

#: How long a place change stays flagged as recent. Long enough to notice on a
#: board that repaints four times a second, short enough that it still means
#: "just now" rather than "at some point".
RECENT_MOVE_WINDOW = timedelta(seconds=12)


@dataclass(frozen=True, slots=True)
class PositionMovement:
    current: int
    #: The first position this collector observed for the driver.
    baseline: int
    #: Positive when places were gained, because moving from 15th to 3rd is a
    #: gain of twelve rather than a change of minus twelve.
    places_gained: int
    #: "up", "down" or None — set only while a change is inside the window.
    recent: str | None


class PositionTracker:
    """Per-driver position history for one session."""

    def __init__(self, *, window: timedelta = RECENT_MOVE_WINDOW) -> None:
        self._window = window
        self._baseline: dict[str, int] = {}
        self._current: dict[str, int] = {}
        self._moved_at: dict[str, datetime] = {}
        self._direction: dict[str, str] = {}

    def observe(self, payload: object, *, received_at: datetime) -> None:
        """Record positions from one merged or partial ``TimingData`` payload.

        Reconnect snapshots are absorbed rather than treated as a restart: a
        baseline is only ever set the first time a driver is seen, so a
        full-state frame mid-session does not reset anyone's movement.
        """
        if not isinstance(payload, Mapping):
            return
        lines = payload.get("Lines")
        if not isinstance(lines, Mapping):
            return
        for number, raw in lines.items():
            if not isinstance(raw, Mapping):
                continue
            position = _whole(raw.get("Position"))
            if position is None or position < 1:
                continue
            key = str(number)
            previous = self._current.get(key)
            if previous is None:
                self._baseline[key] = position
            elif previous != position:
                self._moved_at[key] = received_at
                self._direction[key] = "up" if position < previous else "down"
            self._current[key] = position

    def movement(self, number: str, *, now: datetime) -> PositionMovement | None:
        key = str(number)
        current = self._current.get(key)
        baseline = self._baseline.get(key)
        if current is None or baseline is None:
            return None
        recent = None
        moved_at = self._moved_at.get(key)
        if moved_at is not None and now - moved_at <= self._window:
            recent = self._direction.get(key)
        return PositionMovement(
            current=current,
            baseline=baseline,
            # A lower position number is a better place, so the baseline minus
            # the current position is the number of places gained.
            places_gained=baseline - current,
            recent=recent,
        )

    def __len__(self) -> int:
        return len(self._current)


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value)
    return None
