"""Append-only JSONL logging for one live session.

The log is disposable: the FastF1 archive backfill is the durable record of any
session, so this favours cheap appends over durability. Writes are flushed but
not ``fsync``-ed, and a truncated final line is dropped on replay rather than
repaired.

Log file names embed feed-supplied event and session names, so those values are
slugified to a restricted character set. That is a path-traversal control, and
the resolved path is additionally required to stay inside the log directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import TracebackType

from app.live.frames import LiveFrame, LiveFrameRejectedError

LOG_SUFFIX = ".jsonl"
MAX_SLUG_LENGTH = 48

_ALLOWED_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")


class LiveSessionLogError(ValueError):
    """Raised when a live session log cannot be addressed or opened."""


def slugify(value: object, *, fallback: str = "unknown") -> str:
    """Reduce untrusted text to ``[a-z0-9-]`` so it is safe in a file name."""
    if not isinstance(value, str):
        return fallback
    collapsed = _ALLOWED_SLUG_CHARACTERS.sub("-", value.casefold()).strip("-")
    if not collapsed:
        return fallback
    return collapsed[:MAX_SLUG_LENGTH].strip("-") or fallback


def build_log_path(
    directory: Path,
    *,
    session_date: date,
    event_name: str,
    session_key: str,
) -> Path:
    """Compose the log path for one live session inside ``directory``."""
    name = (
        f"{session_date.isoformat()}"
        f"__{slugify(event_name, fallback='unknown-event')}"
        f"__{slugify(session_key, fallback='unknown-session')}"
        f"{LOG_SUFFIX}"
    )
    candidate = directory / name
    resolved_directory = directory.resolve()
    if candidate.resolve().parent != resolved_directory:
        raise LiveSessionLogError("resolved log path escapes the log directory")
    return candidate


class LiveSessionLog:
    """Append-only writer for one session, bounded by ``max_bytes``."""

    def __init__(self, path: Path, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise LiveSessionLogError("max_bytes must be a positive integer")
        self._path = path
        self._max_bytes = max_bytes
        self._degraded = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._size = path.stat().st_size if path.exists() else 0
        self._handle = path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def bytes_written(self) -> int:
        return self._size

    @property
    def degraded(self) -> bool:
        """True once the size cap has stopped this log from accepting frames."""
        return self._degraded

    def append(self, frame: LiveFrame) -> bool:
        """Append one frame. Returns False when the size cap rejected it."""
        line = f"{frame.to_log_line()}\n"
        encoded_length = len(line.encode("utf-8"))
        if self._size + encoded_length > self._max_bytes:
            # Losing the log is preferable to filling the disk; the caller keeps
            # streaming to clients and reports a log-degraded session.
            self._degraded = True
            return False
        self._handle.write(line)
        self._handle.flush()
        self._size += encoded_length
        return True

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> LiveSessionLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def iter_frames(path: Path) -> Iterator[LiveFrame]:
    """Replay a session log, skipping any malformed or truncated line."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield LiveFrame.from_log_line(stripped)
            except LiveFrameRejectedError:
                continue
