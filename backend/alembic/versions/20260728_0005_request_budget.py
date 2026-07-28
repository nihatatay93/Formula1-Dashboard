"""Add observable global FastF1 request budget.

Revision ID: 20260728_0005
Revises: 20260728_0004
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0005"
down_revision: str | None = "20260728_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_upstream_request_gates_reason"),
        "upstream_request_gates",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_upstream_request_gates_reason"),
        "upstream_request_gates",
        "reason IN ('pacing', 'rate_limit', 'budget')",
    )
    op.create_table(
        "upstream_request_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source = 'fastf1'",
            name=op.f("ck_upstream_request_events_source"),
        ),
        sa.CheckConstraint(
            "operation IN ('archive', 'schedule')",
            name=op.f("ck_upstream_request_events_operation"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_upstream_request_events"),
        ),
    )
    op.create_index(
        op.f("ix_upstream_request_events_requested_at"),
        "upstream_request_events",
        ["requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_upstream_request_events_requested_at"),
        table_name="upstream_request_events",
    )
    op.drop_table("upstream_request_events")
    op.execute(
        "UPDATE upstream_request_gates "
        "SET reason = 'rate_limit' "
        "WHERE reason = 'budget'"
    )
    op.drop_constraint(
        op.f("ck_upstream_request_gates_reason"),
        "upstream_request_gates",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_upstream_request_gates_reason"),
        "upstream_request_gates",
        "reason IN ('pacing', 'rate_limit')",
    )
