"""Process-wide live-service instance and its FastAPI dependency.

Kept separate from ``app.live.api`` so tests and the application lifespan can
replace the instance without importing the router, and so the eventual move to a
standalone container has one obvious seam.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.live.collector import LiveFeedFactory
from app.live.f1_auth import F1TokenStore
from app.live.policy import LiveTimingSettings
from app.live.replay_feed import ReplayFeedError, build_replay_feed_factory
from app.live.service import LiveService
from app.live.signalr_feed import build_signalr_feed_factory

logger = logging.getLogger(__name__)

_service: LiveService | None = None


def feed_requires_authentication(settings: LiveTimingSettings) -> bool:
    """The live feed needs a token; a configured replay does not."""
    return settings.replay_path is None


def build_feed_factory(settings: LiveTimingSettings) -> LiveFeedFactory | None:
    """Resolve the upstream feed.

    A configured recording wins, because replay is an explicit development
    choice and should not be silently overridden by a stored token. Otherwise
    the live SignalR feed is used, which requires an F1 TV token; the token is
    read per connection attempt so signing in makes the feed usable without a
    restart.
    """
    if settings.replay_path is not None:
        try:
            return build_replay_feed_factory(
                Path(settings.replay_path),
                speed=settings.replay_speed,
            )
        except ReplayFeedError:
            # A misconfigured recording must not stop the API from starting.
            logger.warning(
                "live replay recording is unusable, falling back to the live feed",
                exc_info=True,
            )

    tokens = F1TokenStore(
        Path(settings.token_path),
        default_ttl=settings.token_ttl,
    )

    def token_provider() -> str | None:
        return tokens.login_session(now=datetime.now(tz=UTC))

    return build_signalr_feed_factory(token_provider)


def get_live_service() -> LiveService:
    """Return the process-wide live service, building it on first use."""
    global _service
    if _service is None:
        settings = LiveTimingSettings.from_environment()
        _service = LiveService(
            settings=settings,
            feed_factory=build_feed_factory(settings),
            requires_authentication=feed_requires_authentication(settings),
        )
    return _service


def set_live_service(service: LiveService | None) -> None:
    """Replace the process-wide live service. Intended for tests and startup."""
    global _service
    _service = service
