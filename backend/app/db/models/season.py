from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (CheckConstraint("year >= 1950", name="year_minimum"),)

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    coverage_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    coverage_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
