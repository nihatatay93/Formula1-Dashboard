from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.constants import (
    INGESTION_STATUSES,
    RECORD_STATES,
    SOURCES,
    sql_values,
)


class SessionIngestion(TimestampMixin, Base):
    __tablename__ = "session_ingestions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(INGESTION_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        CheckConstraint(
            f"record_state IN ({sql_values(RECORD_STATES)})",
            name="record_state",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        Index(None, "status", "next_retry_at"),
        Index(None, "heartbeat_at"),
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
    source: Mapped[str] = mapped_column(Text)
    record_state: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
    )
    first_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
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
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
