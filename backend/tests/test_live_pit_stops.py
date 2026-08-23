"""Timing the pit lane from the flag the feed does send.

There is no duration in the feed. `PitLaneTimeCollection` exists and arrived
empty throughout the 2026 Dutch Grand Prix, so a stop is timed from `InPit`
turning true to turning false. Measured that way once the race was running,
twenty-three stops fell between 14.8 and 19.4 seconds with nothing outside.
"""

from datetime import UTC, datetime, timedelta

from app.live.pit_stops import PitStopTracker

START = datetime(2026, 8, 23, 13, 40, tzinfo=UTC)


def frame(number: str, in_pit: bool) -> dict[str, object]:
    return {"Lines": {number: {"InPit": in_pit}}}


def test_a_completed_visit_is_timed() -> None:
    tracker = PitStopTracker()
    tracker.observe(frame("44", True), received_at=START, session_status="Started")
    tracker.observe(
        frame("44", False),
        received_at=START + timedelta(seconds=18.6),
        session_status="Started",
    )

    stops = tracker.stops()
    assert len(stops) == 1
    assert stops[0].racing_number == "44"
    assert stops[0].seconds == 18.6


def test_stops_are_ordered_quickest_first() -> None:
    tracker = PitStopTracker()
    for number, seconds in (("1", 19.4), ("43", 14.8), ("23", 16.7)):
        tracker.observe(
            frame(number, True), received_at=START, session_status="Started"
        )
        tracker.observe(
            frame(number, False),
            received_at=START + timedelta(seconds=seconds),
            session_status="Started",
        )

    assert [stop.racing_number for stop in tracker.stops()] == ["43", "23", "1"]


def test_a_visit_spanning_a_red_flag_is_not_a_stop() -> None:
    """Cars sat in the lane through the Dutch stoppage for minutes.

    Every one of those visits began under one session status and ended under
    another, which is what separates them from a racing stop without guessing
    at a duration.
    """
    tracker = PitStopTracker()
    tracker.observe(frame("16", True), received_at=START, session_status="Aborted")
    tracker.observe(
        frame("16", False),
        received_at=START + timedelta(seconds=95),
        session_status="Started",
    )

    assert tracker.stops() == ()


def test_an_implausibly_long_visit_is_not_a_stop() -> None:
    # A backstop for a car that sat in the lane without the session changing
    # around it. No pit lane takes minutes to drive through.
    tracker = PitStopTracker()
    tracker.observe(frame("18", True), received_at=START, session_status="Started")
    tracker.observe(
        frame("18", False),
        received_at=START + timedelta(minutes=9),
        session_status="Started",
    )

    assert tracker.stops() == ()


def test_a_car_still_in_the_lane_has_no_stop_yet() -> None:
    tracker = PitStopTracker()
    tracker.observe(frame("55", True), received_at=START, session_status="Started")

    assert tracker.stops() == ()
    assert tracker.in_pit_lane() == frozenset({"55"})


def test_a_second_entry_without_an_exit_replaces_the_first() -> None:
    # The car never left, so the earlier moment did not start anything that
    # finished, and timing from it would invent a long stop.
    tracker = PitStopTracker()
    tracker.observe(frame("30", True), received_at=START, session_status="Started")
    tracker.observe(
        frame("30", True),
        received_at=START + timedelta(seconds=40),
        session_status="Started",
    )
    tracker.observe(
        frame("30", False),
        received_at=START + timedelta(seconds=58),
        session_status="Started",
    )

    assert [stop.seconds for stop in tracker.stops()] == [18.0]


def test_an_exit_without_an_entry_is_ignored() -> None:
    # A reconnect can deliver a car already out of the pits.
    tracker = PitStopTracker()
    tracker.observe(frame("77", False), received_at=START, session_status="Started")

    assert tracker.stops() == ()


def test_a_payload_without_the_flag_changes_nothing() -> None:
    tracker = PitStopTracker()
    tracker.observe(
        {"Lines": {"1": {"Position": "1"}}},
        received_at=START,
        session_status="Started",
    )
    tracker.observe("not a mapping", received_at=START)

    assert tracker.stops() == ()
    assert tracker.in_pit_lane() == frozenset()


def test_a_car_already_in_the_lane_on_a_snapshot_is_never_timed() -> None:
    """The feed reports current state on connect and on every reconnect.

    The whole grid sits in the pit lane before a race, so timing from the
    moment collection happened to start produced a six-second stop in the 2026
    Dutch Grand Prix -- quicker than any pit lane can be driven. When the entry
    was not observed, the duration is unknowable.
    """
    tracker = PitStopTracker()
    tracker.observe(
        frame("77", True),
        received_at=START,
        session_status="Inactive",
        initial=True,
    )
    tracker.observe(
        frame("77", False),
        received_at=START + timedelta(seconds=6),
        session_status="Inactive",
    )

    assert tracker.stops() == ()


def test_a_later_real_stop_is_still_timed_after_a_snapshot_entry() -> None:
    # Ignoring the snapshot must not poison the car for the rest of the race.
    tracker = PitStopTracker()
    tracker.observe(
        frame("77", True), received_at=START, session_status="Started", initial=True
    )
    tracker.observe(
        frame("77", False),
        received_at=START + timedelta(seconds=6),
        session_status="Started",
    )
    tracker.observe(
        frame("77", True),
        received_at=START + timedelta(minutes=20),
        session_status="Started",
    )
    tracker.observe(
        frame("77", False),
        received_at=START + timedelta(minutes=20, seconds=19.4),
        session_status="Started",
    )

    assert [stop.seconds for stop in tracker.stops()] == [19.4]


def test_a_stop_is_timed_on_the_feed_clock_not_on_arrival() -> None:
    """Arrival time is a different clock from the session's.

    A replay delivers a recorded session faster than it happened, which turned
    twenty-second stops into two-tenths when this timed on arrival. The feed
    stamps its own frames, and that is what a duration has to measure.
    """
    tracker = PitStopTracker()
    tracker.observe(
        frame("44", True),
        received_at=START,
        feed_timestamp=START,
        session_status="Started",
    )
    tracker.observe(
        frame("44", False),
        # Arrived a fifth of a second later; happened eighteen seconds later.
        received_at=START + timedelta(seconds=0.2),
        feed_timestamp=START + timedelta(seconds=18.6),
        session_status="Started",
    )

    assert [stop.seconds for stop in tracker.stops()] == [18.6]


def test_arrival_time_is_used_when_the_feed_sends_no_stamp() -> None:
    # A small share of frames carry none; they still have to be timed.
    tracker = PitStopTracker()
    tracker.observe(frame("5", True), received_at=START, session_status="Started")
    tracker.observe(
        frame("5", False),
        received_at=START + timedelta(seconds=17.1),
        session_status="Started",
    )

    assert [stop.seconds for stop in tracker.stops()] == [17.1]
