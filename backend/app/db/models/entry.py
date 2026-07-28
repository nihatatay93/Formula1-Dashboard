from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.constants import RECORD_STATES, SOURCES, sql_values


class SessionEntry(TimestampMixin, Base):
    __tablename__ = "session_entries"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({sql_values(SOURCES)})",
            name="source",
        ),
        CheckConstraint(
            f"record_state IN ({sql_values(RECORD_STATES)})",
            name="record_state",
        ),
        UniqueConstraint("session_id", "entry_key"),
        Index(
            "uq_session_entries_session_racing_number",
            "session_id",
            "racing_number",
            unique=True,
            postgresql_where=text("racing_number IS NOT NULL"),
        ),
        Index(
            "uq_session_entries_session_driver",
            "session_id",
            "driver_id",
            unique=True,
            postgresql_where=text("driver_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="RESTRICT"),
    )
    driver_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("drivers.id", ondelete="RESTRICT"),
    )
    entry_key: Mapped[str] = mapped_column(Text)
    racing_number: Mapped[str | None] = mapped_column(Text)
    abbreviation: Mapped[str | None] = mapped_column(Text)
    broadcast_name: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    team_jolpica_id: Mapped[str | None] = mapped_column(Text)
    team_name: Mapped[str | None] = mapped_column(Text)
    team_color: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    record_state: Mapped[str] = mapped_column(Text)
