from datetime import datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
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


class LapTelemetryIngestion(TimestampMixin, Base):
    __tablename__ = "lap_telemetry_ingestions"
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
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("sample_count >= 0", name="sample_count_nonnegative"),
        Index(None, "status", "next_retry_at", "requested_at"),
        Index(None, "heartbeat_at"),
    )

    lap_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("laps.id", ondelete="CASCADE"),
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
    sample_count: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
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
    source_snapshot_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)


class LapTelemetrySample(TimestampMixin, Base):
    __tablename__ = "lap_telemetry_samples"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        CheckConstraint(
            f"record_state IN ({sql_values(RECORD_STATES)})",
            name="record_state",
        ),
        CheckConstraint("sample_index >= 0", name="sample_index_nonnegative"),
        CheckConstraint("lap_time_us >= 0", name="lap_time_us_nonnegative"),
        # Measured from the session start, so a sample taken before it is
        # negative. See the note on `laps`.
        CheckConstraint(
            "distance_m IS NULL OR distance_m >= 0",
            name="distance_m_nonnegative",
        ),
        CheckConstraint(
            "relative_distance IS NULL OR "
            "(relative_distance >= 0 AND relative_distance <= 1.01)",
            name="relative_distance_range",
        ),
        CheckConstraint(
            "speed_kph IS NULL OR speed_kph >= 0",
            name="speed_kph_nonnegative",
        ),
        CheckConstraint("rpm IS NULL OR rpm >= 0", name="rpm_nonnegative"),
        CheckConstraint(
            "gear IS NULL OR (gear >= 0 AND gear <= 20)",
            name="gear_range",
        ),
        CheckConstraint(
            "throttle_percent IS NULL OR "
            "(throttle_percent >= 0 AND throttle_percent <= 100)",
            name="throttle_percent_range",
        ),
        CheckConstraint(
            "drs IS NULL OR (drs >= 0 AND drs <= 20)",
            name="drs_range",
        ),
        UniqueConstraint("lap_id", "sample_index"),
        Index(None, "lap_id", "sample_index"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    lap_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("laps.id", ondelete="CASCADE"),
    )
    sample_index: Mapped[int] = mapped_column(Integer)
    lap_time_us: Mapped[int] = mapped_column(BigInteger)
    session_time_us: Mapped[int | None] = mapped_column(BigInteger)
    distance_m: Mapped[float | None] = mapped_column(REAL)
    relative_distance: Mapped[float | None] = mapped_column(REAL)
    speed_kph: Mapped[float | None] = mapped_column(REAL)
    rpm: Mapped[int | None] = mapped_column(Integer)
    gear: Mapped[int | None] = mapped_column(SmallInteger)
    throttle_percent: Mapped[float | None] = mapped_column(REAL)
    brake: Mapped[bool | None] = mapped_column(Boolean)
    drs: Mapped[int | None] = mapped_column(SmallInteger)
    x: Mapped[float | None] = mapped_column(REAL)
    y: Mapped[float | None] = mapped_column(REAL)
    z: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str] = mapped_column(Text)
    record_state: Mapped[str] = mapped_column(Text)
