"""Discovery and safe addressing of recorded session logs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.recordings import (
    RecordingNotFoundError,
    list_recordings,
    resolve_recording,
)


def write(directory: Path, name: str, *, body: str = "{}\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class TestListing:
    def test_an_absent_directory_lists_nothing(self, tmp_path: Path) -> None:
        assert list_recordings(tmp_path / "missing") == ()

    def test_identity_is_recovered_from_the_file_name(self, tmp_path: Path) -> None:
        write(tmp_path, "2026-07-25__hungarian-grand-prix__qualifying.jsonl")

        (recording,) = list_recordings(tmp_path)

        assert recording.name == "2026-07-25__hungarian-grand-prix__qualifying.jsonl"
        assert recording.event_name == "Hungarian Grand Prix"
        assert recording.session_key == "Qualifying"
        assert recording.session_date is not None
        assert recording.session_date.isoformat() == "2026-07-25"
        assert recording.size_bytes > 0

    def test_only_session_logs_are_offered(self, tmp_path: Path) -> None:
        write(tmp_path, "2026-07-25__gp__race.jsonl")
        write(tmp_path, "notes.txt")
        write(tmp_path, "token.json")

        assert [item.name for item in list_recordings(tmp_path)] == [
            "2026-07-25__gp__race.jsonl"
        ]

    def test_newest_recording_comes_first(self, tmp_path: Path) -> None:
        older = write(tmp_path, "2026-07-25__gp__practice-1.jsonl")
        newer = write(tmp_path, "2026-07-26__gp__race.jsonl")
        stale = (datetime.now(tz=UTC) - timedelta(days=2)).timestamp()
        import os

        os.utime(older, (stale, stale))

        assert [item.name for item in list_recordings(tmp_path)] == [
            newer.name,
            older.name,
        ]

    def test_a_file_outside_the_naming_convention_is_still_offered(
        self, tmp_path: Path
    ) -> None:
        # It is replayable; it simply has no identity to display.
        write(tmp_path, "capture.jsonl")

        (recording,) = list_recordings(tmp_path)

        assert recording.name == "capture.jsonl"
        assert recording.session_date is None
        assert recording.session_key == ""

    def test_the_listing_is_json_safe(self, tmp_path: Path) -> None:
        import json

        write(tmp_path, "2026-07-25__gp__race.jsonl")

        json.dumps([item.as_dict() for item in list_recordings(tmp_path)])


class TestResolution:
    def test_a_known_recording_resolves(self, tmp_path: Path) -> None:
        write(tmp_path, "2026-07-25__gp__race.jsonl")

        resolved = resolve_recording(tmp_path, "2026-07-25__gp__race.jsonl")

        assert resolved.parent == tmp_path.resolve()

    @pytest.mark.parametrize(
        "name",
        [
            "../secrets.jsonl",
            "../../etc/passwd.jsonl",
            "nested/race.jsonl",
            "/etc/passwd.jsonl",
            "..%2Fsecrets.jsonl",
            "race.jsonl\x00.txt",
            "",
            "   ",
            None,
            17,
        ],
    )
    def test_a_name_that_could_escape_the_directory_is_refused(
        self, tmp_path: Path, name: object
    ) -> None:
        with pytest.raises(RecordingNotFoundError):
            resolve_recording(tmp_path, name)

    def test_a_non_log_suffix_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "token.json")

        with pytest.raises(RecordingNotFoundError):
            resolve_recording(tmp_path, "token.json")

    def test_a_symlink_out_of_the_directory_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.jsonl"
        secret.write_text("{}\n", encoding="utf-8")
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "linked.jsonl").symlink_to(secret)

        # The name passes the character check, so the resolved parent is what
        # actually stops it.
        with pytest.raises(RecordingNotFoundError):
            resolve_recording(logs, "linked.jsonl")

    def test_an_absent_recording_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RecordingNotFoundError):
            resolve_recording(tmp_path, "2026-07-25__gp__race.jsonl")
