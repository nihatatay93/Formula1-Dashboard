"""Process-wide live-timing service.

Owns at most one live session, the retention sweep loop, and the directory-level
size cap. This runs inside the API process for now; because it shares no table,
lock, or request budget with the archive path, it can be extracted into its own
Compose service later without touching the archive code.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.live.collector import (
    LiveCollector,
    LiveFeedFactory,
    LiveSessionIdentity,
)
from app.live.f1_auth import F1TokenStore, StoredToken
from app.live.policy import LiveTimingSettings
from app.live.retention import (
    SweepResult,
    directory_size_bytes,
    sweep_expired_logs,
)

STOP_GRACE_SECONDS = 2.0


class LiveServiceError(RuntimeError):
    """Base error for live-service command failures."""


class LiveFeedUnconfiguredError(LiveServiceError):
    """Raised when no upstream feed provider has been configured."""


class LiveSessionConflictError(LiveServiceError):
    """Raised when a different live session is already active."""


class LiveService:
    def __init__(
        self,
        *,
        settings: LiveTimingSettings,
        feed_factory: LiveFeedFactory | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._feed_factory = feed_factory
        self._clock = clock if clock is not None else lambda: datetime.now(tz=UTC)
        self._tokens = F1TokenStore(
            Path(settings.token_path),
            default_ttl=settings.token_ttl,
        )
        self._collector: LiveCollector | None = None
        self._task: asyncio.Task[None] | None = None
        self._retention_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def settings(self) -> LiveTimingSettings:
        return self._settings

    @property
    def log_directory(self) -> Path:
        return Path(self._settings.log_directory)

    @property
    def active(self) -> LiveCollector | None:
        return self._collector

    @property
    def tokens(self) -> F1TokenStore:
        return self._tokens

    @property
    def companion_auth_url(self) -> str:
        """Primes the companion extension with this API's port, then sends the
        browser to formula1.com to sign in."""
        return (
            "https://f1login.fastf1.dev"
            f"?port={self._settings.auth_callback_port}"
        )

    def authentication_status(self) -> dict[str, object]:
        """Auth state plus the one-click sign-in entry point.

        The dashboard reads this through live status, so the companion URL has
        to travel with it rather than only from the auth endpoint.
        """
        status = dict(self._tokens.status(now=self._clock()))
        status["companion_url"] = self.companion_auth_url
        return status

    def save_token(self, login_session: object) -> StoredToken:
        return self._tokens.save(login_session, now=self._clock())

    def clear_token(self) -> bool:
        return self._tokens.clear()

    @property
    def feed_configured(self) -> bool:
        return self._feed_factory is not None

    def configure_feed(self, feed_factory: LiveFeedFactory | None) -> None:
        self._feed_factory = feed_factory

    async def start_session(self, identity: LiveSessionIdentity) -> LiveCollector:
        """Start collection, or return the already-running identical session."""
        if self._feed_factory is None:
            raise LiveFeedUnconfiguredError(
                "no live feed provider is configured in this deployment"
            )
        async with self._lock:
            existing = self._collector
            if existing is not None and self._task is not None and not self._task.done():
                if existing.identity == identity:
                    return existing
                raise LiveSessionConflictError(
                    "a different live session is already active"
                )

            logging_enabled = self._prepare_log_directory()
            collector = LiveCollector(
                identity=identity,
                feed_factory=self._feed_factory,
                settings=self._settings,
                log_directory=self.log_directory,
                clock=self._clock,
                logging_enabled=logging_enabled,
            )
            self._collector = collector
            self._task = asyncio.create_task(collector.run())
            return collector

    async def stop_session(self) -> bool:
        """Stop the active session. Returns False when none was running."""
        async with self._lock:
            collector = self._collector
            task = self._task
            self._collector = None
            self._task = None
        if collector is None or task is None:
            return False

        collector.request_stop()
        # The run loop only observes the stop request between frames, so a feed
        # blocked upstream is cancelled rather than waited on indefinitely.
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=STOP_GRACE_SECONDS)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return True

    def sweep_now(self) -> SweepResult:
        return sweep_expired_logs(
            self.log_directory,
            now=self._clock(),
            retention=self._settings.retention,
        )

    async def run_retention_loop(self) -> None:
        """Sweep at startup and on the configured interval until cancelled."""
        while True:
            with contextlib.suppress(OSError):
                self.sweep_now()
            await asyncio.sleep(
                self._settings.retention_sweep_interval.total_seconds()
            )

    async def startup(self) -> None:
        if self._retention_task is None or self._retention_task.done():
            self._retention_task = asyncio.create_task(self.run_retention_loop())

    async def shutdown(self) -> None:
        await self.stop_session()
        task = self._retention_task
        self._retention_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def status(self) -> dict[str, object]:
        collector = self._collector
        running = (
            collector is not None and self._task is not None and not self._task.done()
        )
        return {
            "active": running,
            "feed_configured": self.feed_configured,
            "retention_days": self._settings.retention_days,
            "log_directory_bytes": directory_size_bytes(self.log_directory),
            "max_directory_bytes": self._settings.max_directory_bytes,
            "authentication": self.authentication_status(),
            "session": None if collector is None else collector.status(),
        }

    def _prepare_log_directory(self) -> bool:
        """Enforce the directory cap, returning whether logging may proceed."""
        directory = self.log_directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        if directory_size_bytes(directory) <= self._settings.max_directory_bytes:
            return True
        with contextlib.suppress(OSError):
            self.sweep_now()
        # Still over the cap after sweeping: stream without logging rather than
        # refusing the session or filling the disk.
        return directory_size_bytes(directory) <= self._settings.max_directory_bytes
