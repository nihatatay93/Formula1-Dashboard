from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.live.frames import LiveFrame, normalize_frame
from app.live.session_log import (
    LiveSessionLog,
    LiveSessionLogError,
    build_log_path,
    iter_frames,
    slugify,
)

RECEIVED_AT = datetime(2026, 7, 25, 14, 25, 51, tzinfo=UTC)


def _position(frame_: LiveFrame) -> object:
    return frame_.payload["Lines"]["1"]["Position"]


def frame(marker: int, topic: str = "TimingData") -> LiveFrame:
    return normalize_frame(
        topic,
        {"Lines": {"1": {"Position": str(marker)}}},
        received_at=RECEIVED_AT,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Dutch Grand Prix", "dutch-grand-prix"),
        ("  Práctica 1  ", "pr-ctica-1"),
        ("../../etc/passwd", "etc-passwd"),
        ("/absolute/path", "absolute-path"),
        ("....", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        (42, "unknown"),
    ],
)
def test_slugify_reduces_untrusted_text_to_a_safe_token(
    value: object,
    expected: str,
) -> None:
    assert slugify(value) == expected


def test_slugify_truncates_without_leaving_a_trailing_separator() -> None:
    assert len(slugify("a" * 200)) == 48


def test_build_log_path_composes_a_dated_session_name(tmp_path: Path) -> None:
    path = build_log_path(
        tmp_path,
        session_date=date(2026, 8, 21),
        event_name="Dutch Grand Prix",
        session_key="qualifying",
    )

    assert path.name == "2026-08-21__dutch-grand-prix__qualifying.jsonl"
    assert path.parent == tmp_path


def test_build_log_path_contains_a_traversal_attempt(tmp_path: Path) -> None:
    path = build_log_path(
        tmp_path,
        session_date=date(2026, 8, 21),
        event_name="../../../etc",
        session_key="../passwd",
    )

    assert path.parent == tmp_path
    assert path.name == "2026-08-21__etc__passwd.jsonl"


def test_append_writes_one_line_per_frame(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"

    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        assert log.append(frame(1)) is True
        assert log.append(frame(2)) is True
        assert log.degraded is False

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_append_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "session.jsonl"

    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        log.append(frame(1))

    assert path.exists()


def test_reopening_a_log_appends_rather_than_truncating(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"

    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        log.append(frame(1))
    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        assert log.bytes_written > 0
        log.append(frame(2))

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_size_cap_stops_appending_and_marks_the_log_degraded(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    first = frame(1)
    cap = len(f"{first.to_log_line()}\n".encode())

    with LiveSessionLog(path, max_bytes=cap) as log:
        assert log.append(first) is True
        assert log.append(frame(2)) is False
        assert log.degraded is True

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_non_positive_cap_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LiveSessionLogError, match="max_bytes"):
        LiveSessionLog(tmp_path / "session.jsonl", max_bytes=0)


def test_iter_frames_replays_written_frames_in_order(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        for sequence in (1, 2, 3):
            log.append(frame(sequence))

    assert [_position(item) for item in iter_frames(path)] == ["1", "2", "3"]


def test_iter_frames_drops_a_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with LiveSessionLog(path, max_bytes=1_000_000) as log:
        log.append(frame(1))
        log.append(frame(2))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"received_at":"2026-07-25T14:25:51Z","topic":"Timing')

    assert [_position(item) for item in iter_frames(path)] == ["1", "2"]


def test_iter_frames_skips_blank_and_corrupt_interior_lines(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    good = frame(1).to_log_line()
    later = frame(2).to_log_line()
    path.write_text(f"{good}\n\nnot-json\n{later}\n", encoding="utf-8")

    assert [_position(item) for item in iter_frames(path)] == ["1", "2"]


def test_iter_frames_on_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_frames(tmp_path / "absent.jsonl")) == []
