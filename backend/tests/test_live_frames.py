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

RECEIVED_AT = datetime(2026, 7, 25, 14, 25, 51, 937_000, tzinfo=UTC)


def normalized(
    payload: object,
    topic: str = "TimingData",
    **kwargs: object,
) -> LiveFrame:
    return normalize_frame(topic, payload, received_at=RECEIVED_AT, **kwargs)


def rejection_reason(**kwargs: object) -> FrameRejection:
    with pytest.raises(LiveFrameRejectedError) as caught:
        normalize_frame(
            kwargs.get("topic", "TimingData"),
            kwargs.get("payload", {}),
            received_at=kwargs.get("received_at", RECEIVED_AT),
            initial=kwargs.get("initial", False),
            feed_timestamp=kwargs.get("feed_timestamp"),
        )
    return caught.value.reason


def test_accepts_a_consumed_topic_with_a_nested_payload() -> None:
    frame = normalized({"Lines": {"44": {"Position": "3", "GapToLeader": "+1.204"}}})

    assert frame.topic == "TimingData"
    assert frame.initial is False
    assert frame.feed_timestamp is None
    assert frame.payload == {"Lines": {"44": {"Position": "3", "GapToLeader": "+1.204"}}}


def test_unknown_topic_is_rejected_rather_than_stored() -> None:
    assert rejection_reason(topic="SomeFutureTopic") == FrameRejection.UNKNOWN_TOPIC
    assert rejection_reason(topic="") == FrameRejection.UNKNOWN_TOPIC
    assert rejection_reason(topic=None) == FrameRejection.UNKNOWN_TOPIC


@pytest.mark.parametrize(
    "topic",
    ["CarData.z", "Position.z", "ContentStreams", "AudioStreams"],
)
def test_deliberately_ignored_topics_are_distinguished_from_unknown(
    topic: str,
) -> None:
    assert rejection_reason(topic=topic) == FrameRejection.IGNORED_TOPIC


def test_team_radio_is_consumed_rather_than_dropped() -> None:
    """It names one capture per driver, which is timing content, not media.

    The feed gives a car number, a moment and an audio path -- never a
    transcript, which broadcasts add themselves. ContentStreams beside it
    really is a media stream: a commentary URL and an HLS audio feed, with
    nothing per driver, so that one stays ignored.
    """
    frame = normalized(
        {
            "Captures": [
                {
                    "Utc": "2026-08-23T13:06:37.663Z",
                    "RacingNumber": "3",
                    "Path": "TeamRadio/VER_3_20260823_150618.mp3",
                }
            ]
        },
        topic="TeamRadio",
    )

    assert frame.topic == "TeamRadio"
    assert frame.payload["Captures"][0]["RacingNumber"] == "3"


def test_initial_flag_must_be_a_boolean() -> None:
    assert rejection_reason(initial="yes") == FrameRejection.MALFORMED_PAYLOAD
    assert normalized({}, initial=True).initial is True


def test_empty_feed_timestamp_becomes_absent() -> None:
    # The feed sends "" on initial frames.
    assert normalized({}, initial=True, feed_timestamp="").feed_timestamp is None
    assert normalized({}, feed_timestamp=None).feed_timestamp is None
    assert normalized({}, feed_timestamp="   ").feed_timestamp is None


def test_high_precision_feed_timestamp_is_parsed() -> None:
    frame = normalized({}, feed_timestamp="2026-07-25T14:43:27.7867398Z")

    assert frame.feed_timestamp == datetime(
        2026, 7, 25, 14, 43, 27, 786_739, tzinfo=UTC
    )


def test_unparseable_feed_timestamp_is_rejected() -> None:
    assert rejection_reason(feed_timestamp="nonsense") == (
        FrameRejection.INVALID_TIMESTAMP
    )
    assert rejection_reason(feed_timestamp=17) == FrameRejection.INVALID_TIMESTAMP


def test_naive_received_at_is_rejected() -> None:
    naive = datetime(2026, 7, 25, 14, 25, 51)

    assert rejection_reason(received_at=naive) == FrameRejection.INVALID_TIMESTAMP


@pytest.mark.parametrize("payload", [None, [], "compressed-string", 7])
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


def test_real_feed_nesting_depth_is_accepted() -> None:
    # Lines > 14 > Sectors > 1 > Segments > 0 > Status is the observed shape.
    frame = normalized(
        {"Lines": {"14": {"Sectors": {"1": {"Segments": {"0": {"Status": 2051}}}}}}}
    )

    assert frame.payload["Lines"]["14"]["Sectors"]["1"]["Segments"]["0"] == {
        "Status": 2051
    }


def test_sensitive_keys_are_redacted_at_any_depth() -> None:
    frame = normalized(
        {
            "Authorization": "Bearer abc",
            "nested": {"api_key": "k-1", "cookie": "c=1", "Position": "4"},
            "sessionToken": "t-9",
        }
    )

    assert frame.payload["Authorization"] == REDACTED
    assert frame.payload["sessionToken"] == REDACTED
    nested = frame.payload["nested"]
    assert isinstance(nested, dict)
    assert nested["api_key"] == REDACTED
    assert nested["cookie"] == REDACTED
    assert nested["Position"] == "4"


def test_log_line_round_trip_preserves_the_frame() -> None:
    frame = normalized(
        {"Lines": {"1": {"Position": "1"}}, "flag": True, "gap": 1.25},
        feed_timestamp="2026-07-25T14:43:27.786Z",
        initial=False,
    )

    assert LiveFrame.from_log_line(frame.to_log_line()) == frame


def test_log_line_round_trip_preserves_an_initial_frame() -> None:
    frame = normalized({"Status": "1"}, topic="TrackStatus", initial=True)

    restored = LiveFrame.from_log_line(frame.to_log_line())

    assert restored == frame
    assert restored.initial is True
    assert restored.feed_timestamp is None


def test_log_line_normalizes_a_non_utc_timestamp_to_utc() -> None:
    offset = timezone(timedelta(hours=2))
    frame = normalize_frame(
        "TrackStatus",
        {"Status": "1"},
        received_at=datetime(2026, 7, 25, 16, 25, 51, tzinfo=offset),
    )

    assert '"received_at":"2026-07-25T14:25:51Z"' in frame.to_log_line()


@pytest.mark.parametrize(
    "line",
    [
        '{"received_at":"2026-07-25T14:25:51Z","topic":"TimingData"',
        "not json at all",
        "{}",
        '{"received_at":"nonsense","topic":"TimingData","payload":{}}',
        '{"received_at":"2026-07-25T14:25:51Z","topic":"Nope","payload":{}}',
        '{"received_at":"2026-07-25T14:25:51Z","topic":"CarData.z","payload":{}}',
    ],
)
def test_malformed_log_lines_are_rejected(line: str) -> None:
    with pytest.raises(LiveFrameRejectedError):
        LiveFrame.from_log_line(line)
