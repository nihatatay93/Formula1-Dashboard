"""Persist deferred current-season event membership.

Revision ID: 20260728_0007
Revises: 20260728_0006
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0007"
down_revision: str | None = "20260728_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deferred_season_events",
        sa.Column("season_year", sa.SmallInteger(), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column(
            "scheduled_start_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "round_number >= 1",
            name=op.f(
                "ck_deferred_season_events_round_number_positive"
            ),
        ),
        sa.CheckConstraint(
            "length(btrim(event_name)) > 0",
            name=op.f(
                "ck_deferred_season_events_event_name_nonempty"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["season_year"],
            ["seasons.year"],
            name=op.f(
                "fk_deferred_season_events_season_year_seasons"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "season_year",
            "round_number",
            name=op.f("pk_deferred_season_events"),
        ),
    )
    op.create_index(
        op.f(
            "ix_deferred_season_events_season_year_discovered_at_round_number"
        ),
        "deferred_season_events",
        ["season_year", "discovered_at", "round_number"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f(
            "ix_deferred_season_events_season_year_discovered_at_round_number"
        ),
        table_name="deferred_season_events",
    )
    op.drop_table("deferred_season_events")
