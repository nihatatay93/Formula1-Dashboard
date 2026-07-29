"""Normalization of untrusted SignalR live-timing frames.

Upstream frames are untrusted input. Unknown topics, unserializable payloads,
and absurdly nested payloads are rejected with a reason so the collector can
count and drop them instead of writing them to a session log.

Connection credentials cannot reach a frame, because they live in the client
configuration rather than the feed. The redaction pass here is defence in depth
against accidentally embedding one, not the primary control.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

MAX_PAYLOAD_DEPTH = 16
REDACTED = "[redacted]"

#: Documented feed topics required for session state, entry identity, lap
#: timing, and track status. Everything else is dropped.
LIVE_TOPICS: frozenset[str] = frozenset(
    {
        "SessionInfo",
        "SessionStatus",
        "LapCount",
        "DriverList",
        "TimingData",
        "TimingAppData",
        "TrackStatus",
        "RaceControlMessages",
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
    INVALID_SEQUENCE = "invalid_sequence"
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
    sequence: int
    payload: Mapping[str, object]

    def to_log_line(self) -> str:
        return json.dumps(
            {
                "received_at": _format_timestamp(self.received_at),
                "topic": self.topic,
                "seq": self.sequence,
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
        raw_timestamp = decoded.get("received_at")
        if not isinstance(raw_timestamp, str):
            raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
        try:
            received_at = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP) from None
        topic = decoded.get("topic")
        if not isinstance(topic, str):
            raise LiveFrameRejectedError(FrameRejection.UNKNOWN_TOPIC)
        return normalize_frame(
            topic,
            decoded.get("payload"),
            received_at=received_at,
            sequence=decoded.get("seq"),
        )


def normalize_frame(
    topic: object,
    payload: object,
    *,
    received_at: datetime,
    sequence: object,
) -> LiveFrame:
    """Validate one upstream frame, or raise ``LiveFrameRejectedError``."""
    if not isinstance(topic, str) or topic not in LIVE_TOPICS:
        raise LiveFrameRejectedError(FrameRejection.UNKNOWN_TOPIC)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise LiveFrameRejectedError(FrameRejection.INVALID_SEQUENCE)
    if not isinstance(received_at, datetime):
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise LiveFrameRejectedError(FrameRejection.INVALID_TIMESTAMP)
    if not isinstance(payload, Mapping):
        raise LiveFrameRejectedError(FrameRejection.MALFORMED_PAYLOAD)

    return LiveFrame(
        received_at=received_at,
        topic=topic,
        sequence=sequence,
        payload=_normalize_mapping(payload, depth=0),
    )


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
