import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.retention import (
    directory_size_bytes,
    sweep_expired_logs,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


def write_log(directory: Path, name: str, *, age_days: float, size: int = 32) -> Path:
    path = directory / name
    path.write_text("x" * size, encoding="utf-8")
    modified = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (modified, modified))
    return path


def test_deletes_only_logs_older_than_the_retention_window(tmp_path: Path) -> None:
    stale = write_log(tmp_path, "2026-08-10__old__race.jsonl", age_days=9)
    fresh = write_log(tmp_path, "2026-08-27__new__race.jsonl", age_days=1)

    result = sweep_expired_logs(tmp_path, now=NOW, retention=timedelta(days=7))

    assert result.deleted == (stale,)
    assert result.retained == (fresh,)
    assert not stale.exists()
    assert fresh.exists()


def test_a_log_exactly_at_the_cutoff_is_retained(tmp_path: Path) -> None:
    boundary = write_log(tmp_path, "2026-08-21__edge__race.jsonl", age_days=7)

    result = sweep_expired_logs(tmp_path, now=NOW, retention=timedelta(days=7))

    assert result.retained == (boundary,)
    assert boundary.exists()


def test_non_log_files_and_directories_are_never_touched(tmp_path: Path) -> None:
    keep_file = write_log(tmp_path, "notes.txt", age_days=99)
    keep_dir = tmp_path / "2026-01-01__dir__race.jsonl"
    keep_dir.mkdir()

    result = sweep_expired_logs(tmp_path, now=NOW, retention=timedelta(days=7))

    assert result.deleted == ()
    assert keep_file.exists()
    assert keep_dir.is_dir()


def test_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    result = sweep_expired_logs(
        tmp_path / "absent",
        now=NOW,
        retention=timedelta(days=7),
    )

    assert result.deleted == ()
    assert result.retained == ()
    assert result.failed == ()


def test_an_undeletable_log_is_reported_without_stopping_the_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = write_log(tmp_path, "2026-08-01__a__race.jsonl", age_days=20)
    second = write_log(tmp_path, "2026-08-02__b__race.jsonl", age_days=20)
    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == first:
            raise OSError("permission denied")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    result = sweep_expired_logs(tmp_path, now=NOW, retention=timedelta(days=7))

    assert result.failed == (first,)
    assert result.deleted == (second,)
    assert first.exists()


def test_naive_now_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        sweep_expired_logs(
            tmp_path,
            now=datetime(2026, 8, 28, 12, 0, 0),
            retention=timedelta(days=7),
        )


@pytest.mark.parametrize("retention", [timedelta(0), timedelta(days=-1)])
def test_non_positive_retention_is_rejected(
    tmp_path: Path,
    retention: timedelta,
) -> None:
    with pytest.raises(ValueError, match="retention"):
        sweep_expired_logs(tmp_path, now=NOW, retention=retention)


def test_directory_size_counts_only_session_logs(tmp_path: Path) -> None:
    write_log(tmp_path, "2026-08-01__a__race.jsonl", age_days=1, size=100)
    write_log(tmp_path, "2026-08-02__b__race.jsonl", age_days=1, size=50)
    write_log(tmp_path, "ignored.txt", age_days=1, size=999)

    assert directory_size_bytes(tmp_path) == 150


def test_directory_size_of_a_missing_directory_is_zero(tmp_path: Path) -> None:
    assert directory_size_bytes(tmp_path / "absent") == 0
