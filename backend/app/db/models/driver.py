from sqlalchemy import BigInteger, Identity, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Driver(TimestampMixin, Base):
    __tablename__ = "drivers"
    __table_args__ = (
        UniqueConstraint("jolpica_driver_id"),
        UniqueConstraint("live_reference"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    jolpica_driver_id: Mapped[str | None] = mapped_column(Text)
    live_reference: Mapped[str | None] = mapped_column(Text)
    given_name: Mapped[str | None] = mapped_column(Text)
    family_name: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(Text)
