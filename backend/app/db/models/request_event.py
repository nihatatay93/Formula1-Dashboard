from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UpstreamRequestEvent(Base):
    __tablename__ = "upstream_request_events"
    __table_args__ = (
        CheckConstraint(
            "source = 'fastf1'",
            name="source",
        ),
        CheckConstraint(
            "operation IN ('archive', 'schedule')",
            name="operation",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
