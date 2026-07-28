from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.constants import SOURCES, sql_values


class RaceSession(TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        UniqueConstraint("event_id", "session_key"),
        Index(None, "event_id", "scheduled_start_at"),
        Index(None, "event_id", "last_discovered_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("events.id", ondelete="RESTRICT"),
    )
    session_key: Mapped[str] = mapped_column(Text)
    session_name: Mapped[str] = mapped_column(Text)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    scheduled_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    source: Mapped[str] = mapped_column(Text)
