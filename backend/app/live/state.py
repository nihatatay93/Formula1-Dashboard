"""Process-wide live-service instance and its FastAPI dependency.

Kept separate from ``app.live.api`` so tests and the application lifespan can
replace the instance without importing the router, and so the eventual move to a
standalone container has one obvious seam.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.live.collector import LiveFeedFactory
from app.live.policy import LiveTimingSettings
from app.live.replay_feed import ReplayFeedError, build_replay_feed_factory
from app.live.service import LiveService

logger = logging.getLogger(__name__)

_service: LiveService | None = None


def build_feed_factory(settings: LiveTimingSettings) -> LiveFeedFactory | None:
    """Resolve the configured upstream feed, if any.

    Only the replay feed exists today. A live SignalR client is a separate
    provider satisfying the same protocol; until one is configured the live
    endpoints report an unconfigured feed rather than pretending to connect.
    """
    if settings.replay_path is None:
        return None
    try:
        return build_replay_feed_factory(
            Path(settings.replay_path),
            speed=settings.replay_speed,
        )
    except ReplayFeedError:
        # A misconfigured recording must not stop the API from starting; the
        # live endpoints simply continue to report an unconfigured feed.
        logger.warning(
            "live replay recording is unusable, live feed stays unconfigured",
            exc_info=True,
        )
        return None


def get_live_service() -> LiveService:
    """Return the process-wide live service, building it on first use."""
    global _service
    if _service is None:
        settings = LiveTimingSettings.from_environment()
        _service = LiveService(
            settings=settings,
            feed_factory=build_feed_factory(settings),
        )
    return _service


def set_live_service(service: LiveService | None) -> None:
    """Replace the process-wide live service. Intended for tests and startup."""
    global _service
    _service = service
