from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.live.frames import (
    MAX_PAYLOAD_DEPTH,
    REDACTED,
    FrameRejection,
    LiveFrame,
    LiveFrameRejectedError,
    normalize_frame,
)

RECEIVED_AT = datetime(2026, 8, 21, 13, 4, 11, 482_000, tzinfo=UTC)


def normalized(payload: object, topic: str = "TimingData", sequence: int = 1) -> LiveFrame:
    return normalize_frame(
        topic,
        payload,
        received_at=RECEIVED_AT,
        sequence=sequence,
    )


def rejection_reason(**kwargs: object) -> FrameRejection:
    with pytest.raises(LiveFrameRejectedError) as caught:
        normalize_frame(
            kwargs.get("topic", "TimingData"),
            kwargs.get("payload", {}),
            received_at=kwargs.get("received_at", RECEIVED_AT),
            sequence=kwargs.get("sequence", 1),
        )
    return caught.value.reason


def test_accepts_a_documented_topic_with_a_nested_payload() -> None:
    frame = normalized({"Lines": {"44": {"Position": 3, "GapToLeader": "+1.204"}}})

    assert frame.topic == "TimingData"
    assert frame.sequence == 1
    assert frame.payload == {"Lines": {"44": {"Position": 3, "GapToLeader": "+1.204"}}}


def test_unknown_topic_is_rejected_rather_than_stored() -> None:
    assert rejection_reason(topic="CarData.z") == FrameRejection.UNKNOWN_TOPIC
    assert rejection_reason(topic="") == FrameRejection.UNKNOWN_TOPIC
    assert rejection_reason(topic=None) == FrameRejection.UNKNOWN_TOPIC


@pytest.mark.parametrize("sequence", [-1, 1.5, True, None, "4"])
def test_invalid_sequence_is_rejected(sequence: object) -> None:
    assert rejection_reason(sequence=sequence) == FrameRejection.INVALID_SEQUENCE


def test_zero_sequence_is_accepted_as_the_first_frame() -> None:
    assert normalized({}, sequence=0).sequence == 0


def test_naive_timestamp_is_rejected() -> None:
    naive = datetime(2026, 8, 21, 13, 4, 11)

    assert rejection_reason(received_at=naive) == FrameRejection.INVALID_TIMESTAMP


@pytest.mark.parametrize("payload", [None, [], "TimingData", 7])
def test_non_mapping_payload_is_rejected(payload: object) -> None:
    assert rejection_reason(payload=payload) == FrameRejection.MALFORMED_PAYLOAD


def test_non_string_payload_key_is_rejected() -> None:
    assert rejection_reason(payload={3: "x"}) == FrameRejection.MALFORMED_PAYLOAD


def test_non_finite_float_is_rejected_because_it_is_not_valid_json() -> None:
    assert rejection_reason(payload={"gap": float("nan")}) == (
        FrameRejection.MALFORMED_PAYLOAD
    )
    assert rejection_reason(payload={"gap": float("inf")}) == (
        FrameRejection.MALFORMED_PAYLOAD
    )


def test_binary_payload_value_is_rejected_not_treated_as_a_sequence() -> None:
    assert rejection_reason(payload={"raw": b"\x00\x01"}) == (
        FrameRejection.MALFORMED_PAYLOAD
    )


def test_excessively_nested_payload_is_rejected() -> None:
    payload: dict[str, object] = {"leaf": 1}
    for _ in range(MAX_PAYLOAD_DEPTH + 2):
        payload = {"nested": payload}

    assert rejection_reason(payload=payload) == FrameRejection.PAYLOAD_TOO_DEEP


def test_sensitive_keys_are_redacted_at_any_depth() -> None:
    frame = normalized(
        {
            "Authorization": "Bearer abc",
            "nested": {"api_key": "k-1", "cookie": "c=1", "Position": 4},
            "sessionToken": "t-9",
        }
    )

    assert frame.payload["Authorization"] == REDACTED
    assert frame.payload["sessionToken"] == REDACTED
    nested = frame.payload["nested"]
    assert isinstance(nested, dict)
    assert nested["api_key"] == REDACTED
    assert nested["cookie"] == REDACTED
    assert nested["Position"] == 4


def test_log_line_round_trip_preserves_the_frame() -> None:
    frame = normalized({"Lines": {"1": {"Position": 1}}, "flag": True, "gap": 1.25})

    restored = LiveFrame.from_log_line(frame.to_log_line())

    assert restored == frame


def test_log_line_normalizes_a_non_utc_timestamp_to_utc() -> None:
    offset = timezone(timedelta(hours=2))
    frame = normalize_frame(
        "TrackStatus",
        {"Status": "1"},
        received_at=datetime(2026, 8, 21, 15, 4, 11, tzinfo=offset),
        sequence=2,
    )

    assert '"received_at":"2026-08-21T13:04:11Z"' in frame.to_log_line()


@pytest.mark.parametrize(
    "line",
    [
        '{"received_at":"2026-08-21T13:04:11Z","topic":"TimingData","seq":1',
        "not json at all",
        "{}",
        '{"received_at":"nonsense","topic":"TimingData","seq":1,"payload":{}}',
        '{"received_at":"2026-08-21T13:04:11Z","topic":"Nope","seq":1,"payload":{}}',
    ],
)
def test_malformed_log_lines_are_rejected(line: str) -> None:
    with pytest.raises(LiveFrameRejectedError):
        LiveFrame.from_log_line(line)
