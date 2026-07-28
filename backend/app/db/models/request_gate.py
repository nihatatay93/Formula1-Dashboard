from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UpstreamRequestGate(TimestampMixin, Base):
    __tablename__ = "upstream_request_gates"
    __table_args__ = (
        CheckConstraint(
            "char_length(source) > 0",
            name="source_nonempty",
        ),
        CheckConstraint(
            "reason IN ('pacing', 'rate_limit', 'budget')",
            name="reason",
        ),
    )

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    next_request_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    reason: Mapped[str] = mapped_column(Text)
