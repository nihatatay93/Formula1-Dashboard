import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.collector import RawFrame
from app.live.policy import LiveTimingSettings
from app.live.service import LiveFeedUnconfiguredError, LiveService

NOW = datetime(2026, 8, 21, 13, 0, 0, tzinfo=UTC)


class IdleFeed:
    """Stays connected without producing frames until cancelled."""

    def __init__(self) -> None:
        self.closed = False

    async def stream(self) -> AsyncIterator[RawFrame]:
        await asyncio.Event().wait()
        yield RawFrame("TimingData", {})  # pragma: no cover

    async def close(self) -> None:
        self.closed = True


class SingleFrameFeed:
    async def stream(self) -> AsyncIterator[RawFrame]:
        yield RawFrame("TimingData", {"Lines": {"1": {}}}, initial=True)
        await asyncio.Event().wait()

    async def close(self) -> None:
        return None


def service(tmp_path: Path, **overrides: object) -> LiveService:
    return LiveService(
        settings=LiveTimingSettings(log_directory=str(tmp_path), **overrides),
        feed_factory=IdleFeed,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_starting_without_a_configured_feed_is_refused(tmp_path: Path) -> None:
    unconfigured = LiveService(
        settings=LiveTimingSettings(log_directory=str(tmp_path)),
        clock=lambda: NOW,
    )

    with pytest.raises(LiveFeedUnconfiguredError):
        await unconfigured.start_session()

    assert unconfigured.feed_configured is False
    assert unconfigured.active is None


@pytest.mark.asyncio
async def test_start_then_stop_owns_exactly_one_session(tmp_path: Path) -> None:
    live = service(tmp_path)

    collector = await live.start_session()
    assert live.active is collector
    assert live.status()["active"] is True

    assert await live.stop_session() is True
    assert live.active is None
    assert live.status()["active"] is False


@pytest.mark.asyncio
async def test_starting_again_returns_the_running_session(tmp_path: Path) -> None:
    live = service(tmp_path)
    first = await live.start_session()

    # There is only ever "the session that is on now", so a second start is a
    # reuse rather than a conflict.
    assert await live.start_session() is first

    await live.stop_session()


@pytest.mark.asyncio
async def test_stopping_when_idle_reports_no_session(tmp_path: Path) -> None:
    assert await service(tmp_path).stop_session() is False


@pytest.mark.asyncio
async def test_stop_cancels_a_feed_that_never_yields(tmp_path: Path) -> None:
    live = service(tmp_path)
    await live.start_session()

    # IdleFeed blocks forever, so this exercises the cancel path rather than a
    # cooperative stop between frames.
    stopped = await asyncio.wait_for(live.stop_session(), timeout=5)

    assert stopped is True
    assert live.active is None


@pytest.mark.asyncio
async def test_collected_frames_are_visible_through_the_service(
    tmp_path: Path,
) -> None:
    live = LiveService(
        settings=LiveTimingSettings(log_directory=str(tmp_path)),
        feed_factory=SingleFrameFeed,
        clock=lambda: NOW,
    )
    collector = await live.start_session()
    await asyncio.sleep(0.05)

    assert collector.stats.accepted == 1
    assert "TimingData" in collector.view.topics
    await live.stop_session()


@pytest.mark.asyncio
async def test_directory_cap_disables_logging_but_still_streams(
    tmp_path: Path,
) -> None:
    import os

    # Over the cap and inside the retention window, so a sweep cannot reclaim
    # it. The mtime is pinned relative to the injected clock rather than left at
    # the real file time, which would fall outside the fake retention window.
    existing = tmp_path / "2026-08-21__existing__race.jsonl"
    existing.write_text("x" * 500, encoding="utf-8")
    recent = (NOW - timedelta(hours=1)).timestamp()
    os.utime(existing, (recent, recent))
    live = LiveService(
        settings=LiveTimingSettings(
            log_directory=str(tmp_path),
            max_log_bytes=100,
            max_directory_bytes=100,
        ),
        feed_factory=SingleFrameFeed,
        clock=lambda: NOW,
    )

    collector = await live.start_session()
    await asyncio.sleep(0.05)

    assert collector.log_degraded is True
    assert collector.stats.accepted == 1
    assert collector.stats.dropped_by_log_cap == 1
    await live.stop_session()


@pytest.mark.asyncio
async def test_directory_cap_sweeps_expired_logs_before_disabling_logging(
    tmp_path: Path,
) -> None:
    import os

    stale = tmp_path / "2026-01-01__stale__race.jsonl"
    stale.write_text("x" * 500, encoding="utf-8")
    expired = (NOW - timedelta(days=30)).timestamp()
    os.utime(stale, (expired, expired))

    live = LiveService(
        settings=LiveTimingSettings(
            log_directory=str(tmp_path),
            max_log_bytes=100,
            max_directory_bytes=100,
        ),
        feed_factory=SingleFrameFeed,
        clock=lambda: NOW,
    )

    collector = await live.start_session()

    assert not stale.exists()
    assert collector.log_degraded is False
    await live.stop_session()


def test_sweep_now_deletes_expired_logs(tmp_path: Path) -> None:
    import os

    stale = tmp_path / "2026-01-01__stale__race.jsonl"
    stale.write_text("data", encoding="utf-8")
    expired = (NOW - timedelta(days=30)).timestamp()
    os.utime(stale, (expired, expired))

    result = service(tmp_path).sweep_now()

    assert result.deleted == (stale,)


@pytest.mark.asyncio
async def test_startup_schedules_a_sweep_and_shutdown_cancels_it(
    tmp_path: Path,
) -> None:
    import os

    stale = tmp_path / "2026-01-01__stale__race.jsonl"
    stale.write_text("data", encoding="utf-8")
    expired = (NOW - timedelta(days=30)).timestamp()
    os.utime(stale, (expired, expired))
    live = service(tmp_path)

    await live.startup()
    await asyncio.sleep(0.05)

    assert not stale.exists()

    await live.shutdown()
    assert live.active is None


@pytest.mark.asyncio
async def test_shutdown_stops_an_active_session(tmp_path: Path) -> None:
    live = service(tmp_path)
    await live.start_session()

    await asyncio.wait_for(live.shutdown(), timeout=5)

    assert live.active is None


def test_status_reports_directory_usage_and_configuration(tmp_path: Path) -> None:
    (tmp_path / "2026-08-21__a__race.jsonl").write_text("12345", encoding="utf-8")

    status = service(tmp_path, retention_days=3).status()

    assert status["active"] is False
    assert status["feed_configured"] is True
    assert status["retention_days"] == 3
    assert status["log_directory_bytes"] == 5
    assert status["session"] is None
