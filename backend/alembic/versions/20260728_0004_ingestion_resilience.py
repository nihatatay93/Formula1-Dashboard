"""Add archive request gating and preserve unknown lap-best flags.

Revision ID: 20260728_0004
Revises: 20260728_0003
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0004"
down_revision: str | None = "20260728_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "laps",
        "is_personal_best",
        existing_type=sa.Boolean(),
        nullable=True,
    )
    op.create_table(
        "upstream_request_gates",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "next_request_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
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
            "char_length(source) > 0",
            name=op.f(
                "ck_upstream_request_gates_source_nonempty"
            ),
        ),
        sa.CheckConstraint(
            "reason IN ('pacing', 'rate_limit')",
            name=op.f("ck_upstream_request_gates_reason"),
        ),
        sa.PrimaryKeyConstraint(
            "source",
            name=op.f("pk_upstream_request_gates"),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO upstream_request_gates "
            "(source, next_request_at, reason) "
            "VALUES ('fastf1_archive', now(), 'pacing')"
        )
    )


def downgrade() -> None:
    op.drop_table("upstream_request_gates")
    op.execute(
        "UPDATE laps SET is_personal_best = false "
        "WHERE is_personal_best IS NULL"
    )
    op.alter_column(
        "laps",
        "is_personal_best",
        existing_type=sa.Boolean(),
        nullable=False,
    )
