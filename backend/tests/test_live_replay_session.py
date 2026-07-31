"""Replaying a recorded session log through the live pipeline."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.live.board import build_board
from app.live.collector import CollectorState, LiveCollector
from app.live.policy import LiveTimingSettings
from app.live.replay_feed import ReplayFeed, build_replay_feed_factory
from app.live.service import LiveService, LiveSessionBusyError
from app.live.session_log import build_log_path

FIXTURE = Path(__file__).parent / "fixtures" / "live_signalr_qualifying.jsonl"
BASE = datetime(2026, 7, 25, 14, 0, 0, tzinfo=UTC)


def settings_for(directory: Path) -> LiveTimingSettings:
    return LiveTimingSettings(log_directory=str(directory), replay_speed=1_000_000.0)


def write_session_log(directory: Path) -> Path:
    """Convert the capture fixture into this application's own log format.

    The two shapes differ, which is the point: a session log spells the feed's
    instant ``feed_timestamp`` and carries ``received_at``, while a capture file
    spells it ``timestamp`` and has no ``received_at``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = build_log_path(
        directory,
        session_date=BASE.date(),
        event_name="Hungarian Grand Prix",
        session_key="Qualifying",
    )
    lines = []
    for index, raw in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines()):
        if not raw.strip():
            continue
        record = json.loads(raw)
        feed_timestamp = record.get("timestamp") or None
        lines.append(
            json.dumps(
                {
                    "received_at": (
                        BASE.replace(microsecond=index * 1000)
                    ).isoformat(),
                    "topic": record["topic"],
                    "initial": bool(record.get("initial")),
                    "feed_timestamp": feed_timestamp,
                    "payload": record["payload"],
                },
                separators=(",", ":"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
class TestSessionLogPacing:
    """A session log spells the feed's instant differently from a capture file.

    Reading only ``timestamp`` would emit an entire recorded session in one
    burst, so this asserts the log's own spellings are honoured.
    """

    async def drain(self, path: Path, *, speed: float = 1.0) -> list[float]:
        slept: list[float] = []

        async def sleep(seconds: float) -> None:
            slept.append(seconds)

        feed = ReplayFeed(path, speed=speed, sleep=sleep)
        async for _ in feed.stream():
            pass
        return slept

    async def test_a_session_log_is_paced_by_its_feed_timestamp(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "received_at": "2026-07-25T14:00:00+00:00",
                        "topic": "Heartbeat",
                        "initial": False,
                        "feed_timestamp": stamp,
                        "payload": {},
                    }
                )
                for stamp in (
                    "2026-07-25T14:00:00+00:00",
                    "2026-07-25T14:00:04+00:00",
                    "2026-07-25T14:00:06+00:00",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        assert await self.drain(path) == [4.0, 2.0]

    async def test_received_at_paces_frames_the_feed_did_not_stamp(
        self, tmp_path: Path
    ) -> None:
        # The feed sends initial frames with an empty timestamp, which the log
        # stores as null; received_at is the only ordering those frames have.
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "received_at": stamp,
                        "topic": "Heartbeat",
                        "initial": True,
                        "feed_timestamp": None,
                        "payload": {},
                    }
                )
                for stamp in (
                    "2026-07-25T14:00:00+00:00",
                    "2026-07-25T14:00:03+00:00",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        assert await self.drain(path) == [3.0]

    async def test_the_feed_timestamp_reaches_the_frame(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text(
            json.dumps(
                {
                    "received_at": "2026-07-25T14:00:00+00:00",
                    "topic": "Heartbeat",
                    "initial": False,
                    "feed_timestamp": "2026-07-25T14:00:02+00:00",
                    "payload": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        frames = [frame async for frame in ReplayFeed(path).stream()]

        assert frames[0].timestamp == "2026-07-25T14:00:02+00:00"

    async def test_a_capture_file_is_still_paced_by_its_own_spelling(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "capture.jsonl"
        path.write_text(
            "\n".join(
                json.dumps({"topic": "Heartbeat", "payload": {}, "timestamp": stamp})
                for stamp in (
                    "2026-07-25T14:00:00+00:00",
                    "2026-07-25T14:00:05+00:00",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        assert await self.drain(path) == [5.0]


@pytest.mark.asyncio
class TestReplaySession:
    async def test_a_session_log_replays_into_real_merged_state(
        self, tmp_path: Path
    ) -> None:
        path = write_session_log(tmp_path)
        service = LiveService(settings=settings_for(tmp_path))

        collector = await service.start_replay(path.name)
        await service._task

        view = collector.view
        assert view.topics["SessionInfo"].payload["Type"] == "Qualifying"
        assert len(view.topics["TimingData"].payload["Lines"]) == 22
        board = build_board(
            {topic: state.payload for topic, state in view.topics.items()}
        )
        assert board.meeting_name == "Hungarian Grand Prix"
        assert len(board.drivers) == 22

    async def test_a_replay_never_writes_to_the_recording_it_reads(
        self, tmp_path: Path
    ) -> None:
        """The identity a replay derives resolves to its own source file."""
        path = write_session_log(tmp_path)
        before = path.read_bytes()
        service = LiveService(settings=settings_for(tmp_path))

        collector = await service.start_replay(path.name)
        await service._task

        # It did read the session, and it did name it — the two facts that make
        # appending possible in the first place.
        assert collector.stats.accepted > 0
        assert collector.identity is not None
        assert collector.identity.event_name == "Hungarian Grand Prix"

        assert path.read_bytes() == before, "the source recording was modified"
        assert collector.log_path is None
        assert list(tmp_path.glob("*.jsonl")) == [path], "a replay wrote a new log"

    async def test_replay_alone_prevents_logging_even_if_it_is_enabled(
        self, tmp_path: Path
    ) -> None:
        """Defence in depth: the flag holds without the service's help.

        The service already passes ``logging_enabled=False``, so this asks the
        collector directly with logging on — the configuration that would append
        a replay's frames straight back into the file it is reading.
        """
        path = write_session_log(tmp_path)
        before = path.read_bytes()
        collector = LiveCollector(
            feed_factory=build_replay_feed_factory(path, speed=1_000_000.0),
            settings=settings_for(tmp_path),
            log_directory=tmp_path,
            logging_enabled=True,
            replay=True,
        )

        await collector.run()

        assert collector.stats.accepted > 0
        assert collector.identity is not None
        assert path.read_bytes() == before
        assert list(tmp_path.glob("*.jsonl")) == [path]

    async def test_a_replay_is_not_reported_as_a_degraded_log(
        self, tmp_path: Path
    ) -> None:
        # Writing no log is intentional here, so the dashboard must not warn.
        path = write_session_log(tmp_path)
        service = LiveService(settings=settings_for(tmp_path))

        collector = await service.start_replay(path.name)
        await service._task

        assert collector.replay is True
        assert collector.log_degraded is False
        assert collector.stats.dropped_by_log_cap == 0
        assert collector.status()["replay"] is True

    async def test_the_recording_ends_the_session_instead_of_reconnecting(
        self, tmp_path: Path
    ) -> None:
        path = write_session_log(tmp_path)
        service = LiveService(settings=settings_for(tmp_path))

        collector = await service.start_replay(path.name)
        await service._task

        assert collector.state is CollectorState.FINISHED
        assert collector.finished is True
        assert collector.stats.reconnects == 0
        assert collector.stats.connection_attempts == 1
        # The finished session stays addressable so its final state is readable.
        assert service.active is collector
        assert service.status()["active"] is False
        assert service.status()["session"]["finished"] is True

    async def test_a_replay_needs_no_f1_tv_token(self, tmp_path: Path) -> None:
        path = write_session_log(tmp_path)
        service = LiveService(
            settings=settings_for(tmp_path),
            requires_authentication=True,
        )

        collector = await service.start_replay(path.name)
        await service._task

        assert collector.stats.accepted > 0

    async def test_a_replay_is_refused_while_a_session_is_running(
        self, tmp_path: Path
    ) -> None:
        path = write_session_log(tmp_path)
        service = LiveService(settings=settings_for(tmp_path))
        never_ends = _never_ending_feed()
        service.configure_feed(lambda: never_ends)
        await service.start_session()

        try:
            with pytest.raises(LiveSessionBusyError):
                await service.start_replay(path.name)
        finally:
            await service.stop_session()

    async def test_stopping_a_replay_releases_the_slot(self, tmp_path: Path) -> None:
        path = write_session_log(tmp_path)
        service = LiveService(settings=settings_for(tmp_path))

        await service.start_replay(path.name)
        await service._task
        assert await service.stop_session() is True

        assert service.active is None
        assert service.status()["session"] is None
        # A finished replay must not block the next one.
        await service.start_replay(path.name)
        await service._task


class _Feed:
    finite = False

    def __init__(self, event) -> None:
        self._event = event

    async def stream(self):
        await self._event.wait()
        return
        yield  # pragma: no cover - never reached

    async def close(self) -> None:
        return None


def _never_ending_feed() -> _Feed:
    import asyncio

    return _Feed(asyncio.Event())
