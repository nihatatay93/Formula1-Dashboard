"""Retention sweep for disposable live session logs.

Deletion is unconditionally safe: a log older than the archive availability
grace has already been superseded by the FastF1 backfill of the same session,
and nothing in the application reads these files except live replay. The sweep
never touches PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.live.session_log import LOG_SUFFIX


@dataclass(frozen=True, slots=True)
class SweepResult:
    deleted: tuple[Path, ...]
    retained: tuple[Path, ...]
    failed: tuple[Path, ...]

    @property
    def deleted_count(self) -> int:
        return len(self.deleted)


def directory_size_bytes(directory: Path) -> int:
    """Total size of session logs in ``directory``, ignoring anything else."""
    if not directory.is_dir():
        return 0
    return sum(path.stat().st_size for path in _session_logs(directory))


def sweep_expired_logs(
    directory: Path,
    *,
    now: datetime,
    retention: timedelta,
) -> SweepResult:
    """Delete session logs last modified before ``now - retention``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if retention <= timedelta(0):
        raise ValueError("retention must be positive")
    if not directory.is_dir():
        return SweepResult(deleted=(), retained=(), failed=())

    cutoff = now - retention
    deleted: list[Path] = []
    retained: list[Path] = []
    failed: list[Path] = []

    for path in _session_logs(directory):
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            failed.append(path)
            continue
        if modified_at >= cutoff:
            retained.append(path)
            continue
        try:
            path.unlink()
        except OSError:
            # A sweep failure must never stop the live service.
            failed.append(path)
            continue
        deleted.append(path)

    return SweepResult(
        deleted=tuple(deleted),
        retained=tuple(retained),
        failed=tuple(failed),
    )


def _session_logs(directory: Path) -> Sequence[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix == LOG_SUFFIX
    )
