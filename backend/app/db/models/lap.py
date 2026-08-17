from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.constants import RECORD_STATES, SOURCES, sql_values


class Lap(TimestampMixin, Base):
    __tablename__ = "laps"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        CheckConstraint(
            f"record_state IN ({sql_values(RECORD_STATES)})",
            name="record_state",
        ),
        # Only durations are constrained non-negative. The session-relative
        # instants below them -- session_time_us, lap_start_time_us, the pit
        # times and the sector session times -- deliberately are not: FastF1
        # measures them from a session's official start, and a car already on
        # track before that start carries a negative one. Rejecting those cost
        # a whole practice session for the sake of thirteen out laps.
        CheckConstraint("lap_number >= 1", name="lap_number_positive"),
        CheckConstraint(
            "stint_number IS NULL OR stint_number >= 1",
            name="stint_number_positive",
        ),
        CheckConstraint(
            "lap_time_us IS NULL OR lap_time_us >= 0",
            name="lap_time_us_nonnegative",
        ),
        CheckConstraint(
            "sector_1_time_us IS NULL OR sector_1_time_us >= 0",
            name="sector_1_time_us_nonnegative",
        ),
        CheckConstraint(
            "sector_2_time_us IS NULL OR sector_2_time_us >= 0",
            name="sector_2_time_us_nonnegative",
        ),
        CheckConstraint(
            "sector_3_time_us IS NULL OR sector_3_time_us >= 0",
            name="sector_3_time_us_nonnegative",
        ),
        CheckConstraint(
            "speed_i1_kph IS NULL OR speed_i1_kph >= 0",
            name="speed_i1_kph_nonnegative",
        ),
        CheckConstraint(
            "speed_i2_kph IS NULL OR speed_i2_kph >= 0",
            name="speed_i2_kph_nonnegative",
        ),
        CheckConstraint(
            "speed_fl_kph IS NULL OR speed_fl_kph >= 0",
            name="speed_fl_kph_nonnegative",
        ),
        CheckConstraint(
            "speed_st_kph IS NULL OR speed_st_kph >= 0",
            name="speed_st_kph_nonnegative",
        ),
        CheckConstraint(
            "tyre_life_laps IS NULL OR tyre_life_laps >= 0",
            name="tyre_life_laps_nonnegative",
        ),
        CheckConstraint(
            "position IS NULL OR position >= 1",
            name="position_positive",
        ),
        UniqueConstraint("session_entry_id", "lap_number"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    session_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session_entries.id", ondelete="RESTRICT"),
    )
    lap_number: Mapped[int] = mapped_column(SmallInteger)
    stint_number: Mapped[int | None] = mapped_column(SmallInteger)
    session_time_us: Mapped[int | None] = mapped_column(BigInteger)
    lap_time_us: Mapped[int | None] = mapped_column(BigInteger)
    lap_start_time_us: Mapped[int | None] = mapped_column(BigInteger)
    pit_out_time_us: Mapped[int | None] = mapped_column(BigInteger)
    pit_in_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_1_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_2_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_3_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_1_session_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_2_session_time_us: Mapped[int | None] = mapped_column(BigInteger)
    sector_3_session_time_us: Mapped[int | None] = mapped_column(BigInteger)
    speed_i1_kph: Mapped[float | None] = mapped_column(REAL)
    speed_i2_kph: Mapped[float | None] = mapped_column(REAL)
    speed_fl_kph: Mapped[float | None] = mapped_column(REAL)
    speed_st_kph: Mapped[float | None] = mapped_column(REAL)
    is_personal_best: Mapped[bool | None] = mapped_column(Boolean)
    compound: Mapped[str | None] = mapped_column(Text)
    tyre_life_laps: Mapped[int | None] = mapped_column(SmallInteger)
    fresh_tyre: Mapped[bool | None] = mapped_column(Boolean)
    track_status: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(SmallInteger)
    deleted: Mapped[bool | None] = mapped_column(Boolean)
    deleted_reason: Mapped[str | None] = mapped_column(Text)
    fastf1_generated: Mapped[bool] = mapped_column(Boolean)
    is_accurate: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)
    record_state: Mapped[str] = mapped_column(Text)
