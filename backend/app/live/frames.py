"""Normalization of untrusted SignalR live-timing frames.

The wire format is confirmed against a recorded Hungarian Grand Prix 2026
qualifying session: each frame is ``{topic, payload, timestamp, initial}``.
There is no sequence number. A connect delivers one ``initial`` frame per topic
carrying full state, and every later frame is a deep partial delta.

Upstream frames are untrusted input. Unknown topics, unserializable payloads and
absurdly nested payloads are rejected with a reason so the collector can count
and drop them. Topics that are deliberately out of scope are rejected as
``IGNORED_TOPIC`` rather than ``UNKNOWN_TOPIC``, so a genuinely new topic stays
visible in the counters.

Connection credentials cannot reach a frame, because they live in the client
configuration rather than the feed. The redaction pass here is defence in depth.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

MAX_PAYLOAD_DEPTH = 24
REDACTED = "[redacted]"

#: Topics whose state the live view is built from.
CONSUMED_TOPICS: frozenset[str] = frozenset(
    {
        "DriverList",
        "ExtrapolatedClock",
        "Heartbeat",
        "LapCount",
        "RaceControlMessages",
        "SessionData",
        "SessionInfo",
        "SessionStatus",
        "TimingAppData",
        "TimingData",
        "TimingStats",
        "TopThree",
        "TrackStatus",
        "WeatherData",
    }
)

#: Known topics that are deliberately dropped. CarData.z and Position.z are
#: base64 raw-deflate car telemetry and track coordinates: roughly 39% of frames
#: in a recorded session, and outside the timing scope of the live view. The
#: remainder are media streams.
IGNORED_TOPICS: frozenset[str] = frozenset(
    {
        "AudioStreams",
        "CarData.z",
        "ContentStreams",
        "Position.z",
        "TeamRadio",
    }
)

SENSITIVE_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "apikey",
        "api_key",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)


class FrameRejection(StrEnum):
    UNKNOWN_TOPIC = "unknown_topic"
    IGNORED_TOPIC = "ignored_topic"
    INVALID_TIMESTAMP = "invalid_timestamp"
    MALFORMED_PAYLOAD = "malformed_payload"
    PAYLOAD_TOO_DEEP = "payload_too_deep"


class LiveFrameRejectedError(ValueError):
    """Raised when an upstream frame cannot be accepted."""

    def __init__(self, reason: FrameRejection) -> None:
        super().__init__(f"live frame rejected: {reason.value}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LiveFrame:
    received_at: datetime
    topic: str
    #: True for a connect snapshot, which replaces topic state instead of
    #: merging into it.
    initial: bool
    #: The feed's own timestamp. Absent on initial frames, which carry "".
    feed_timestamp: datetime | None
    payload: Mapping[str, object]

    def to_log_line(self) -> str:
        return json.dumps(
            {
                "received_at": _format_timestamp(self.received_at),
                "topic": self.topic,
                "initial": self.initial,
                "feed_timestamp": (
                    None
                    if self.feed_timestamp is None
                    else _format_timestamp(self.feed_timestamp)
                ),
                "payload": self.payload,
            },
            separators=(",", ":"),
            allow_nan=False,
            sort_keys=True,
        )

    @classmethod
    def from_log_line(cls, line: str) -> LiveFrame:
        try:
            decoded = json.loads(line)
        except ValueError:
            raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD) from None
        if not isinstance(decoded, Mapping):
            raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)
        raw_received = decoded.get("received_at")
        if not isinstance(raw_received, str):
            raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
        received_at = _parse_timestamp(raw_received)
        if received_at is None:
            raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
        topic = decoded.get("topic")
        return normalize_frame(
            topic if isinstance(topic, str) else None,
            decoded.get("payload"),
            received_at=received_at,
            initial=decoded.get("initial"),
            feed_timestamp=decoded.get("feed_timestamp"),
        )


def normalize_frame(
    topic: object,
    payload: object,
    *,
    received_at: datetime,
    initial: object = False,
    feed_timestamp: object = None,
) -> LiveFrame:
    """Validate one upstream frame, or raise ``LiveFrameRejectedError``."""
    if not isinstance(topic, str) or not topic:
        raise LiveFrameRejectedError(FrameRejection.UNKNOWN_TOPIC)
    if topic in IGNORED_TOPICS:
        raise LiveFrameRejectedError(FrameRejection.IGNORED_TOPIC)
    if topic not in CONSUMED_TOPICS:
        raise LiveFrameRejectedError(FrameRejection.UNKNOWN_TOPIC)
    if not isinstance(initial, bool):
        raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)
    if not isinstance(received_at, datetime):
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    # Only mapping payloads are consumed. The string payloads in the feed belong
    # to the compressed topics, which are already rejected as ignored above.
    if not isinstance(payload, Mapping):
        raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)

    return LiveFrame(
        received_at=received_at,
        topic=topic,
        initial=initial,
        feed_timestamp=_normalize_feed_timestamp(feed_timestamp),
        payload=_normalize_mapping(payload, depth=0),
    )


def _normalize_feed_timestamp(value: object) -> datetime | None:
    """The feed sends "" on initial frames and an ISO instant afterwards."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
        return value
    if not isinstance(value, str):
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    if not value.strip():
        return None
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    return parsed


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _normalize_mapping(
    payload: Mapping[object, object],
    *,
    depth: int,
) -> dict[str, object]:
    if depth > MAX_PAYLOAD_DEPTH:
        raise LiveFrameRejectedError(FrameRejection.PAYLOAD_TOO_DEEP)
    normalized: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)
        if _is_sensitive_key(key):
            normalized[key] = REDACTED
            continue
        normalized[key] = _normalize_value(value, depth=depth + 1)
    return normalized


def _normalize_value(value: object, *, depth: int) -> object:
    if depth > MAX_PAYLOAD_DEPTH:
        raise LiveFrameRejectedError(FrameRejection.PAYLOAD_TOO_DEEP)
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)
        return value
    if isinstance(value, Mapping):
        return _normalize_mapping(value, depth=depth)
    # Rejected before the Sequence branch, which would otherwise accept them.
    if isinstance(value, bytes | bytearray):
        raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)
    if isinstance(value, Sequence):
        return [_normalize_value(item, depth=depth + 1) for item in value]
    raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _format_timestamp(value: datetime) -> str:
    """Always serialize UTC, so a log line never carries a local offset."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
