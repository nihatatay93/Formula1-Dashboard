"""Discovery of recorded session logs available for replay.

A session log is written by the collector during a live session and deleted by
the retention sweep afterwards, so this listing is a view of a window that
closes on its own. Nothing here reads PostgreSQL, and replay never promotes a
recording into the archive.

File names are the only metadata: ``{date}__{event}__{session}.jsonl``, built by
``session_log.build_log_path`` from feed-supplied names that were slugified on
the way in. Parsing them back is lossy — the display name is a best-effort
un-slugify — but it costs no file reads, which matters because a listing must
stay cheap next to a live session.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from app.live.session_log import LOG_SUFFIX

#: Names come from ``build_log_path``, whose slugs are ``[a-z0-9-]``.
RECORDING_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

_NAME_PARTS = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})__(?P<event>[a-z0-9-]+)__(?P<session>[a-z0-9-]+)$"
)


class RecordingNotFoundError(LookupError):
    """Raised when a requested recording cannot be addressed or does not exist."""


@dataclass(frozen=True, slots=True)
class Recording:
    name: str
    event_name: str
    session_key: str
    session_date: date | None
    size_bytes: int
    modified_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "event_name": self.event_name,
            "session_key": self.session_key,
            "session_date": (
                None if self.session_date is None else self.session_date.isoformat()
            ),
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at.isoformat(),
        }


def _titleize(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("-") if word)


def _describe(path: Path) -> Recording:
    stem = path.stem
    match = _NAME_PARTS.match(stem)
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    if match is None:
        # A file that does not follow the naming convention is still replayable;
        # it just has no derivable identity to show.
        return Recording(
            name=path.name,
            event_name=stem,
            session_key="",
            session_date=None,
            size_bytes=stat.st_size,
            modified_at=modified_at,
        )
    try:
        session_date = date.fromisoformat(match.group("date"))
    except ValueError:
        session_date = None
    return Recording(
        name=path.name,
        event_name=_titleize(match.group("event")),
        session_key=_titleize(match.group("session")),
        session_date=session_date,
        size_bytes=stat.st_size,
        modified_at=modified_at,
    )


def list_recordings(directory: Path) -> Sequence[Recording]:
    """Recordings in ``directory``, newest first."""
    if not directory.is_dir():
        return ()
    found = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix != LOG_SUFFIX:
            continue
        try:
            found.append(_describe(path))
        except OSError:
            # A file that vanished mid-listing is simply not offered.
            continue
    return tuple(sorted(found, key=lambda item: item.modified_at, reverse=True))


def resolve_recording(directory: Path, name: object) -> Path:
    """Resolve a caller-supplied recording name inside ``directory``.

    The name is untrusted. It is matched against a restricted character set and
    the resolved path is required to sit directly in the log directory, so
    neither a separator, a ``..`` segment, nor a symlink out of the directory
    can address a file elsewhere on the host.
    """
    if not isinstance(name, str) or not name.strip():
        raise RecordingNotFoundError("a recording name is required")
    if not RECORDING_NAME.match(name) or not name.endswith(LOG_SUFFIX):
        raise RecordingNotFoundError(f"unknown recording: {name}")

    candidate = directory / name
    try:
        resolved = candidate.resolve()
        resolved_directory = directory.resolve()
    except OSError as error:
        raise RecordingNotFoundError(f"unknown recording: {name}") from error
    if resolved.parent != resolved_directory or not resolved.is_file():
        raise RecordingNotFoundError(f"unknown recording: {name}")
    return resolved
