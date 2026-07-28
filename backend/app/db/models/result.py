from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.constants import RECORD_STATES, SOURCES, sql_values


class SessionResult(TimestampMixin, Base):
    __tablename__ = "session_results"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        CheckConstraint(
            f"record_state IN ({sql_values(RECORD_STATES)})",
            name="record_state",
        ),
        CheckConstraint(
            "position IS NULL OR position >= 1",
            name="position_positive",
        ),
        CheckConstraint(
            "grid_position IS NULL OR grid_position >= 0",
            name="grid_position_nonnegative",
        ),
        CheckConstraint(
            "points IS NULL OR points >= 0",
            name="points_nonnegative",
        ),
        CheckConstraint(
            "laps_completed IS NULL OR laps_completed >= 0",
            name="laps_completed_nonnegative",
        ),
        CheckConstraint(
            "q1_time_us IS NULL OR q1_time_us >= 0",
            name="q1_time_us_nonnegative",
        ),
        CheckConstraint(
            "q2_time_us IS NULL OR q2_time_us >= 0",
            name="q2_time_us_nonnegative",
        ),
        CheckConstraint(
            "q3_time_us IS NULL OR q3_time_us >= 0",
            name="q3_time_us_nonnegative",
        ),
        CheckConstraint(
            "elapsed_time_us IS NULL OR elapsed_time_us >= 0",
            name="elapsed_time_us_nonnegative",
        ),
        CheckConstraint(
            "gap_to_leader_us IS NULL OR gap_to_leader_us >= 0",
            name="gap_to_leader_us_nonnegative",
        ),
        CheckConstraint(
            "gap_to_leader_laps IS NULL OR gap_to_leader_laps >= 0",
            name="gap_to_leader_laps_nonnegative",
        ),
    )

    session_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session_entries.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int | None] = mapped_column(SmallInteger)
    classified_position: Mapped[str | None] = mapped_column(Text)
    grid_position: Mapped[int | None] = mapped_column(SmallInteger)
    points: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    status: Mapped[str | None] = mapped_column(Text)
    laps_completed: Mapped[int | None] = mapped_column(SmallInteger)
    q1_time_us: Mapped[int | None] = mapped_column(BigInteger)
    q2_time_us: Mapped[int | None] = mapped_column(BigInteger)
    q3_time_us: Mapped[int | None] = mapped_column(BigInteger)
    elapsed_time_us: Mapped[int | None] = mapped_column(BigInteger)
    gap_to_leader_us: Mapped[int | None] = mapped_column(BigInteger)
    gap_to_leader_laps: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text)
    record_state: Mapped[str] = mapped_column(Text)
