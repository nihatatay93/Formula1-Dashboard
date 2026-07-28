"""Track membership in the latest successful schedule discovery.

Revision ID: 20260728_0003
Revises: 20260728_0002
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0003"
down_revision: str | None = "20260728_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "last_discovered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "last_discovered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_events_season_year_last_discovered_at",
        "events",
        ["season_year", "last_discovered_at"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_event_id_last_discovered_at",
        "sessions",
        ["event_id", "last_discovered_at"],
        unique=False,
    )
    op.execute(
        "UPDATE seasons SET coverage_valid_until = NULL "
        "WHERE coverage_valid_until IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sessions_event_id_last_discovered_at",
        table_name="sessions",
    )
    op.drop_index(
        "ix_events_season_year_last_discovered_at",
        table_name="events",
    )
    op.drop_column("sessions", "last_discovered_at")
    op.drop_column("events", "last_discovered_at")
