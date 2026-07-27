from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.constants import (
    INGESTION_STATUSES,
    REQUEST_REASONS,
    sql_values,
)


class BackfillJob(Base):
    __tablename__ = "backfill_jobs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(INGESTION_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            f"request_reason IN ({sql_values(REQUEST_REASONS)})",
            name="request_reason",
        ),
        Index(None, "status", "requested_at"),
        Index(
            "uq_backfill_jobs_active_season",
            "season_year",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    season_year: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("seasons.year", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        server_default=text("'pending'"),
    )
    request_reason: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)


class BackfillJobSession(Base):
    __tablename__ = "backfill_job_sessions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(INGESTION_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        Index(None, "status", "next_retry_at", "queued_at"),
        Index(None, "job_id", "status"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backfill_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
