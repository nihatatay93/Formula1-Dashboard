"""Times how long each car spends in the pit lane during a live session.

The feed carries no pit duration. ``TimingData`` reports ``InPit`` as a
boolean, and ``PitLaneTimeCollection`` exists but arrived empty throughout the
2026 Dutch Grand Prix, so the only way to a number is to time the flag: a car
is in the lane from when ``InPit`` turns true until it turns false.

Measured that way across the Dutch race once it was running, twenty-three
stops fell between 14.8 and 19.4 seconds with nothing outside -- the shape a
real pit lane produces. The only distortion came from the red flag, where cars
sat in the lane for minutes and every one of those visits spanned a change of
session status. That is the exclusion this uses: a visit that outlives the
session state it began in was not a racing stop.

This is pit-*lane* time, entry to exit, and it is timed at this end rather than
by the circuit's own loops. It is not the stationary figure a broadcast quotes,
which is roughly twenty seconds shorter.

State lives here rather than in the merged topic view for the same reason
position history does: the view holds only what the feed last said, and a
duration is a fact about two moments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

#: A visit longer than this is a car sitting in the lane rather than serving a
#: stop. It is a backstop only -- the session-status rule catches the real
#: cause -- and is deliberately far above any pit lane's transit time.
IMPLAUSIBLE_VISIT = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class PitStop:
    racing_number: str
    #: Seconds in the pit lane, entry to exit.
    seconds: float
    #: The lap the car was on when it entered, when the feed reported one.
    lap_number: int | None
    entered_at: datetime


@dataclass(slots=True)
class _Visit:
    started_at: datetime
    session_status: str
    lap_number: int | None
    #: False when the car was already in the lane on a snapshot, so the moment
    #: it entered is unknown and no duration can be claimed.
    observed_entry: bool


class PitStopTracker:
    """Completed pit-lane visits for one session, quickest first."""

    def __init__(self, *, limit: timedelta = IMPLAUSIBLE_VISIT) -> None:
        self._limit = limit
        self._open: dict[str, _Visit] = {}
        self._stops: list[PitStop] = []

    def observe(
        self,
        payload: object,
        *,
        received_at: datetime,
        session_status: str = "",
        initial: bool = False,
        laps: Mapping[str, int | None] | None = None,
    ) -> None:
        """Record pit entries and exits from one ``TimingData`` payload.

        A car already in the lane on a snapshot is noted but never timed. The
        feed reports current state on connect and on every reconnect, and the
        whole grid sits in the pit lane before a race: timing from the moment
        we happened to connect produced a six-second stop no pit lane could
        deliver. When the entry was not observed, the duration is unknowable.
        """

        if not isinstance(payload, Mapping):
            return
        lines = payload.get("Lines")
        if not isinstance(lines, Mapping):
            return

        for number, line in lines.items():
            if not isinstance(line, Mapping) or "InPit" not in line:
                continue
            in_pit = line.get("InPit")
            if in_pit is True:
                # A second entry without an exit replaces the first: the car
                # never left, so the earlier moment is not the start of
                # anything that finished.
                self._open[str(number)] = _Visit(
                    started_at=received_at,
                    session_status=session_status,
                    lap_number=(laps or {}).get(str(number)),
                    observed_entry=not initial,
                )
                continue
            if in_pit is not False:
                continue
            visit = self._open.pop(str(number), None)
            if visit is None or not visit.observed_entry:
                continue
            elapsed = received_at - visit.started_at
            if elapsed <= timedelta(0) or elapsed >= self._limit:
                continue
            if session_status and visit.session_status != session_status:
                # The session changed while the car was in the lane -- a red
                # flag, or a restart it waited through. Not a racing stop.
                continue
            self._stops.append(
                PitStop(
                    racing_number=str(number),
                    seconds=round(elapsed.total_seconds(), 1),
                    lap_number=visit.lap_number,
                    entered_at=visit.started_at,
                )
            )

    def stops(self) -> tuple[PitStop, ...]:
        """Completed stops, quickest first, then earliest."""

        return tuple(
            sorted(self._stops, key=lambda stop: (stop.seconds, stop.entered_at))
        )

    def in_pit_lane(self) -> frozenset[str]:
        """Cars currently in the lane, whose visit has no duration yet."""

        return frozenset(self._open)
