from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DeferredSeasonEvent(TimestampMixin, Base):
    __tablename__ = "deferred_season_events"
    __table_args__ = (
        CheckConstraint(
            "round_number >= 1",
            name="round_number_positive",
        ),
        CheckConstraint(
            "length(btrim(event_name)) > 0",
            name="event_name_nonempty",
        ),
        Index(None, "season_year", "discovered_at", "round_number"),
    )

    season_year: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("seasons.year", ondelete="CASCADE"),
        primary_key=True,
    )
    round_number: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
    )
    event_name: Mapped[str] = mapped_column(Text)
    scheduled_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
