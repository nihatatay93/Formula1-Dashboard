"""Live SignalR feed for F1 timing.

Implements the same ``LiveFeed`` protocol as the replay feed, so everything
downstream — merge semantics, session log, retention, WebSocket fan-out and the
dashboard — is unchanged.

The endpoint is SignalR Core at ``wss://livetiming.formula1.com/signalrcore``.
The ``signalrcore`` client is synchronous and callback-driven, so the connection
runs on its own thread and frames are handed to the event loop through a queue.
That is deliberate: reusing the client FastF1 uses against this exact endpoint
avoids re-deriving negotiate quirks, the load-balancer cookie, and the auth
header format.

Subscribing delivers one completion message carrying full state per topic, which
becomes the ``initial`` frames, followed by ``feed`` invocations carrying
``[topic, payload, timestamp]`` deltas.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.messages.completion_message import CompletionMessage

from app.live.collector import RawFrame
from app.live.frames import CONSUMED_TOPICS

CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"
NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"

NEGOTIATE_TIMEOUT_SECONDS = 15
CONNECT_TIMEOUT_SECONDS = 30
STOP_TIMEOUT_SECONDS = 5
QUEUE_SIZE = 2048

logger = logging.getLogger(__name__)

#: Sentinel pushed onto the queue when the connection ends, so the async
#: generator can finish and let the collector reconnect.
_CLOSED = object()


class SignalRFeedError(RuntimeError):
    """Raised when a live connection cannot be established."""


class SignalRUnauthenticatedError(SignalRFeedError):
    """Raised when no usable F1 TV token is available."""


class SignalRFeed:
    """One live upstream connection, exposed as an async frame stream."""

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        topics: Sequence[str] | None = None,
        connection_url: str = CONNECTION_URL,
        negotiate_url: str = NEGOTIATE_URL,
        builder: Callable[[], Any] | None = None,
    ) -> None:
        self._token_provider = token_provider
        # Only the topics the view consumes are requested. CarData.z and
        # Position.z were roughly 39% of frames in a recorded session and are
        # out of scope, so not subscribing to them halves the traffic.
        self._topics = list(topics) if topics is not None else sorted(CONSUMED_TOPICS)
        self._connection_url = connection_url
        self._negotiate_url = negotiate_url
        self._builder = builder
        self._connection: Any = None
        self._queue: asyncio.Queue[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    @property
    def topics(self) -> list[str]:
        return list(self._topics)

    def _require_token(self) -> str:
        token = self._token_provider()
        if not token:
            raise SignalRUnauthenticatedError(
                "no valid F1 TV token is stored; sign in again"
            )
        return token

    def _negotiate_headers(self) -> dict[str, str]:
        """Pre-negotiate for the load-balancer cookie the socket needs."""
        response = requests.options(
            self._negotiate_url,
            timeout=NEGOTIATE_TIMEOUT_SECONDS,
        )
        cookie = response.cookies.get("AWSALBCORS")
        return {"Cookie": f"AWSALBCORS={cookie}"} if cookie else {}

    def _build_connection(self, headers: dict[str, str]) -> Any:
        if self._builder is not None:
            return self._builder()
        options = {
            "verify_ssl": True,
            # A factory rather than a value: signalrcore calls it per connect,
            # so a token refreshed between attempts is picked up.
            "access_token_factory": self._require_token,
            "headers": headers,
        }
        return (
            HubConnectionBuilder()
            .with_url(self._connection_url, options=options)
            .build()
        )

    def _publish(self, frame: RawFrame) -> None:
        """Hand a frame to the event loop from the connection thread."""
        loop, queue = self._loop, self._queue
        if loop is None or queue is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._offer, queue, frame)

    @staticmethod
    def _offer(queue: asyncio.Queue[Any], item: Any) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            # A stalled consumer must not block the connection thread; the
            # collector treats a dropped frame the same as a missed delta.
            logger.warning("live frame queue is full, dropping a frame")

    def _on_message(self, message: Any) -> None:
        if isinstance(message, CompletionMessage):
            # The Subscribe response: full state for every topic at once.
            result = message.result or {}
            if isinstance(result, dict):
                for topic, payload in result.items():
                    self._publish(
                        RawFrame(
                            topic=topic,
                            payload=payload,
                            initial=True,
                            timestamp=None,
                        )
                    )
            return

        if isinstance(message, list) and len(message) >= 2:
            timestamp = message[2] if len(message) > 2 else None
            self._publish(
                RawFrame(
                    topic=message[0],
                    payload=message[1],
                    initial=False,
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                )
            )
            return

        logger.debug("ignoring unrecognised live message shape")

    def _on_close(self) -> None:
        loop, queue = self._loop, self._queue
        if loop is None or queue is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._offer, queue, _CLOSED)

    def _start_blocking(self) -> None:
        """Negotiate, connect and subscribe. Runs off the event loop."""
        self._require_token()
        headers = self._negotiate_headers()
        connection = self._build_connection(headers)

        ready = threading.Event()
        connection.on_open(ready.set)
        connection.on_close(self._on_close)
        connection.on("feed", self._on_message)
        connection.start()
        if not ready.wait(timeout=CONNECT_TIMEOUT_SECONDS):
            raise SignalRFeedError("timed out waiting for the live connection")
        connection.send("Subscribe", [self._topics], on_invocation=self._on_message)
        self._connection = connection

    async def stream(self) -> AsyncIterator[RawFrame]:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._closing = False

        await asyncio.to_thread(self._start_blocking)

        queue = self._queue
        while True:
            item = await queue.get()
            if item is _CLOSED or self._closing:
                return
            yield item

    async def close(self) -> None:
        self._closing = True
        connection, self._connection = self._connection, None
        if connection is not None:
            with_stop = getattr(connection, "stop", None)
            if callable(with_stop):
                # Bounded: the client's stop is synchronous, and a socket that
                # never closes must not be able to hang process shutdown.
                with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.to_thread(with_stop),
                        timeout=STOP_TIMEOUT_SECONDS,
                    )
        self._on_close()


def build_signalr_feed_factory(
    token_provider: Callable[[], str | None],
    *,
    topics: Sequence[str] | None = None,
) -> Callable[[], SignalRFeed]:
    """A feed factory the collector can call once per connection attempt."""

    def factory() -> SignalRFeed:
        return SignalRFeed(token_provider=token_provider, topics=topics)

    return factory
