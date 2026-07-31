"""Turns merged live topic state into a display-ready timing board.

The feed's shapes are awkward and differ by session type: a race carries
``GapToLeader``, ``IntervalToPositionAhead`` and a single ``BestLapTime``, while
qualifying carries a three-entry ``BestLapTimes`` and a ``Stats`` array indexed
by session part. Normalising that once here — in Python, against a real recorded
session — keeps the dashboard from re-deriving it in TypeScript without
fixtures.

Every field is derived defensively: the feed is untrusted input, so a missing or
unexpected value yields an empty string rather than an error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.live.positions import PositionTracker

#: Race control history is unbounded over a session; the dashboard shows the
#: most recent messages rather than all of them.
MAX_RACE_CONTROL_MESSAGES = 15

#: Micro-sector status codes, derived from a recorded qualifying session rather
#: than from documentation — the feed publishes no schema for them. Each sector
#: carries a ``Segments`` array whose entries hold one of these codes, and the
#: mapping was established by correlating them against the parent sector's
#: ``PersonalFastest``/``OverallFastest`` flags across 2778 ``TimingData``
#: frames:
#:
#: * sectors flagged neither personal nor overall best are 67% ``2048``;
#: * sectors flagged personal best are 79% ``2049``;
#: * sectors flagged overall best are 44% ``2051``, against 4% elsewhere.
#:
#: ``2051`` is also observed reverting to ``2049`` 27 times — a purple segment
#: being revoked when another driver goes faster, which is exactly the purple
#: semantics. ``2064`` occurred on five fixed track positions (sector 3's last
#: three segments and sector 1's first two) with the driver in a pit-exit state
#: for 151 of its 160 occurrences, so it marks the pit lane rather than a time.
SEGMENT_STATUS = {
    0: "pending",
    2048: "yellow",
    2049: "green",
    2051: "purple",
    2064: "pit",
}

#: A code outside the observed set is rendered neutrally rather than guessed at.
UNKNOWN_SEGMENT_STATUS = "unknown"


@dataclass(frozen=True, slots=True)
class SectorCell:
    value: str
    personal_best: bool
    overall_best: bool
    segments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DriverRow:
    racing_number: str
    tla: str
    full_name: str
    team_name: str
    team_colour: str
    position: int | None
    line: int
    #: Places gained since the collector first saw this driver; positive is a
    #: gain. None until a position has been observed.
    places_gained: int | None
    #: The position that count is measured from, so a reader can tell whether
    #: it means "since the grid" or "since we connected".
    position_baseline: int | None
    #: "up", "down" or "" while a place change is recent enough to flag.
    recent_move: str
    gap_to_leader: str
    interval: str
    last_lap: str
    last_lap_personal_best: bool
    last_lap_overall_best: bool
    best_lap: str
    sectors: tuple[SectorCell, ...]
    compound: str
    tyre_age: int | None
    pit_stops: int | None
    laps: int | None
    in_pit: bool
    pit_out: bool
    retired: bool
    stopped: bool
    knocked_out: bool
    status: str


@dataclass(frozen=True, slots=True)
class RaceControlMessage:
    utc: str
    category: str
    message: str
    lap: int | None
    flag: str


@dataclass(frozen=True, slots=True)
class LiveBoard:
    meeting_name: str
    session_name: str
    session_type: str
    session_status: str
    started: str
    track_status: str
    track_status_code: str
    current_lap: int | None
    total_laps: int | None
    remaining: str
    extrapolating: bool
    weather: dict[str, str] = field(default_factory=dict)
    drivers: tuple[DriverRow, ...] = ()
    race_control: tuple[RaceControlMessage, ...] = ()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _flag(value: object) -> bool:
    return value is True


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value)
    return None


def _ordered(value: object) -> list[Any]:
    """Feed arrays arrive as lists initially and as index-keyed maps in deltas."""
    if isinstance(value, Mapping):
        return [value[key] for key in sorted(value, key=lambda k: _whole(k) or 0)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _segments(value: object) -> tuple[str, ...]:
    return tuple(
        SEGMENT_STATUS.get(status, UNKNOWN_SEGMENT_STATUS)
        if (status := _whole(_mapping(entry).get("Status"))) is not None
        else UNKNOWN_SEGMENT_STATUS
        for entry in _ordered(value)
    )


def _sectors(value: object) -> tuple[SectorCell, ...]:
    return tuple(
        SectorCell(
            value=_text(item.get("Value")) or _text(item.get("PreviousValue")),
            personal_best=_flag(item.get("PersonalFastest")),
            overall_best=_flag(item.get("OverallFastest")),
            segments=_segments(item.get("Segments")),
        )
        for item in (_mapping(entry) for entry in _ordered(value))
    )


def _best_lap(line: Mapping[str, Any], session_part: int | None) -> str:
    """Race exposes one best lap; qualifying exposes one per session part."""
    single = _text(_mapping(line.get("BestLapTime")).get("Value"))
    if single:
        return single
    times = line.get("BestLapTimes")
    if not isinstance(times, Sequence) or isinstance(times, str | bytes):
        return ""
    if session_part is not None and 0 < session_part <= len(times):
        current = _text(_mapping(times[session_part - 1]).get("Value"))
        if current:
            return current
    # Fall back to the last populated part, so a finished session still shows a
    # time rather than an empty cell.
    for entry in reversed(list(times)):
        value = _text(_mapping(entry).get("Value"))
        if value:
            return value
    return ""


def _quali_stats(line: Mapping[str, Any], session_part: int | None) -> Mapping[str, Any]:
    stats = line.get("Stats")
    if not isinstance(stats, Sequence) or isinstance(stats, str | bytes):
        return {}
    if session_part is not None and 0 < session_part <= len(stats):
        return _mapping(stats[session_part - 1])
    return _mapping(stats[-1]) if stats else {}


def _driver_status(line: Mapping[str, Any]) -> str:
    if _flag(line.get("Retired")):
        return "Retired"
    if _flag(line.get("Stopped")):
        return "Stopped"
    if _flag(line.get("InPit")):
        return "In pit"
    if _flag(line.get("PitOut")):
        return "Out lap"
    if _flag(line.get("KnockedOut")):
        return "Knocked out"
    return "On track"


def _tyre(stint_entry: object) -> tuple[str, int | None]:
    for entry in reversed(_ordered(_mapping(stint_entry).get("Stints"))):
        current = _mapping(entry)
        compound = _text(current.get("Compound"))
        if compound:
            return compound, _whole(current.get("TotalLaps"))
    return "", None


def _race_control(payload: Mapping[str, Any]) -> tuple[RaceControlMessage, ...]:
    built = [
        RaceControlMessage(
            utc=_text(item.get("Utc")),
            category=_text(item.get("Category")),
            message=_text(item.get("Message")),
            lap=_whole(item.get("Lap")),
            flag=_text(item.get("Flag")),
        )
        for item in (_mapping(entry) for entry in _ordered(payload.get("Messages")))
    ]
    # Newest first, because that is what a reader looks for.
    return tuple(reversed(built))[:MAX_RACE_CONTROL_MESSAGES]


def build_board(
    topics: Mapping[str, Any],
    *,
    positions: PositionTracker | None = None,
    now: datetime | None = None,
) -> LiveBoard:
    """Build the board from merged topic payloads keyed by topic name.

    ``positions`` carries session history the merged view cannot hold, because
    the view only ever holds what the feed last said. Without it the board is
    still complete; it simply reports no movement.
    """
    moment = now if now is not None else datetime.now(tz=UTC)
    session = _mapping(topics.get("SessionInfo"))
    meeting = _mapping(session.get("Meeting"))
    status = _mapping(topics.get("SessionStatus"))
    track = _mapping(topics.get("TrackStatus"))
    laps = _mapping(topics.get("LapCount"))
    clock = _mapping(topics.get("ExtrapolatedClock"))
    weather = _mapping(topics.get("WeatherData"))
    timing = _mapping(topics.get("TimingData"))
    app_data = _mapping(_mapping(topics.get("TimingAppData")).get("Lines"))
    drivers = _mapping(topics.get("DriverList"))

    session_part = _whole(timing.get("SessionPart"))
    lines = _mapping(timing.get("Lines"))

    rows: list[DriverRow] = []
    for number, raw in lines.items():
        line = _mapping(raw)
        driver = _mapping(drivers.get(number))
        stats = _quali_stats(line, session_part)
        compound, tyre_age = _tyre(app_data.get(number))
        last = _mapping(line.get("LastLapTime"))
        interval = _mapping(line.get("IntervalToPositionAhead"))
        movement = (
            None if positions is None else positions.movement(number, now=moment)
        )

        rows.append(
            DriverRow(
                racing_number=_text(driver.get("RacingNumber")) or number,
                tla=_text(driver.get("Tla")),
                full_name=_text(driver.get("FullName")),
                team_name=_text(driver.get("TeamName")),
                team_colour=_text(driver.get("TeamColour")),
                position=_whole(line.get("Position")),
                line=_whole(line.get("Line")) or _whole(driver.get("Line")) or 0,
                places_gained=None if movement is None else movement.places_gained,
                position_baseline=None if movement is None else movement.baseline,
                recent_move="" if movement is None else (movement.recent or ""),
                gap_to_leader=(
                    _text(line.get("GapToLeader"))
                    or _text(stats.get("TimeDiffToFastest"))
                ),
                interval=(
                    _text(interval.get("Value"))
                    or _text(stats.get("TimeDifftoPositionAhead"))
                ),
                last_lap=_text(last.get("Value")),
                last_lap_personal_best=_flag(last.get("PersonalFastest")),
                last_lap_overall_best=_flag(last.get("OverallFastest")),
                best_lap=_best_lap(line, session_part),
                sectors=_sectors(line.get("Sectors")),
                compound=compound,
                tyre_age=tyre_age,
                pit_stops=_whole(line.get("NumberOfPitStops")),
                laps=_whole(line.get("NumberOfLaps")),
                in_pit=_flag(line.get("InPit")),
                pit_out=_flag(line.get("PitOut")),
                retired=_flag(line.get("Retired")),
                stopped=_flag(line.get("Stopped")),
                knocked_out=_flag(line.get("KnockedOut")),
                status=_driver_status(line),
            )
        )

    # Position when the feed provides it, display line otherwise, so the order
    # stays stable before timing begins.
    rows.sort(key=lambda row: (row.position is None, row.position or row.line))

    return LiveBoard(
        meeting_name=_text(meeting.get("Name")),
        session_name=_text(session.get("Name")),
        session_type=_text(session.get("Type")),
        session_status=_text(status.get("Status")) or _text(session.get("SessionStatus")),
        started=_text(status.get("Started")),
        track_status=_text(track.get("Message")),
        track_status_code=_text(track.get("Status")),
        current_lap=_whole(laps.get("CurrentLap")),
        total_laps=_whole(laps.get("TotalLaps")),
        remaining=_text(clock.get("Remaining")),
        extrapolating=_flag(clock.get("Extrapolating")),
        weather={
            key: _text(value)
            for key, value in weather.items()
            if key != "_kf" and _text(value)
        },
        drivers=tuple(rows),
        race_control=_race_control(_mapping(topics.get("RaceControlMessages"))),
    )


def board_to_dict(board: LiveBoard) -> dict[str, Any]:
    """JSON-safe board for the WebSocket and HTTP surfaces."""
    return {
        "meeting_name": board.meeting_name,
        "session_name": board.session_name,
        "session_type": board.session_type,
        "session_status": board.session_status,
        "started": board.started,
        "track_status": board.track_status,
        "track_status_code": board.track_status_code,
        "current_lap": board.current_lap,
        "total_laps": board.total_laps,
        "remaining": board.remaining,
        "extrapolating": board.extrapolating,
        "weather": dict(board.weather),
        "drivers": [
            {
                "racing_number": row.racing_number,
                "tla": row.tla,
                "full_name": row.full_name,
                "team_name": row.team_name,
                "team_colour": row.team_colour,
                "position": row.position,
                "line": row.line,
                "places_gained": row.places_gained,
                "position_baseline": row.position_baseline,
                "recent_move": row.recent_move,
                "gap_to_leader": row.gap_to_leader,
                "interval": row.interval,
                "last_lap": row.last_lap,
                "last_lap_personal_best": row.last_lap_personal_best,
                "last_lap_overall_best": row.last_lap_overall_best,
                "best_lap": row.best_lap,
                "sectors": [
                    {
                        "value": cell.value,
                        "personal_best": cell.personal_best,
                        "overall_best": cell.overall_best,
                        "segments": list(cell.segments),
                    }
                    for cell in row.sectors
                ],
                "compound": row.compound,
                "tyre_age": row.tyre_age,
                "pit_stops": row.pit_stops,
                "laps": row.laps,
                "in_pit": row.in_pit,
                "pit_out": row.pit_out,
                "retired": row.retired,
                "stopped": row.stopped,
                "knocked_out": row.knocked_out,
                "status": row.status,
            }
            for row in board.drivers
        ],
        "race_control": [
            {
                "utc": item.utc,
                "category": item.category,
                "message": item.message,
                "lap": item.lap,
                "flag": item.flag,
            }
            for item in board.race_control
        ],
    }
