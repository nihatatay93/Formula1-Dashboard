"""Process-wide live-service instance and its FastAPI dependency.

Kept separate from ``app.live.api`` so tests and the application lifespan can
replace the instance without importing the router, and so the eventual move to a
standalone container has one obvious seam.
"""

from __future__ import annotations

from app.live.policy import LiveTimingSettings
from app.live.service import LiveService

_service: LiveService | None = None


def get_live_service() -> LiveService:
    """Return the process-wide live service, building it on first use."""
    global _service
    if _service is None:
        _service = LiveService(settings=LiveTimingSettings.from_environment())
    return _service


def set_live_service(service: LiveService | None) -> None:
    """Replace the process-wide live service. Intended for tests and startup."""
    global _service
    _service = service
