"""Add bounded lap telemetry persistence.

Revision ID: 20260728_0006
Revises: 20260728_0005
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0006"
down_revision: str | None = "20260728_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_upstream_request_events_operation"),
        "upstream_request_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_upstream_request_events_operation"),
        "upstream_request_events",
        "operation IN ('archive', 'schedule', 'telemetry')",
    )
    op.create_table(
        "lap_telemetry_ingestions",
        sa.Column("lap_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("record_state", sa.Text(), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "sample_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("first_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "source_snapshot_completed_at",
            sa.DateTime(timezone=True),
        ),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("last_error_message", sa.Text()),
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
            "status IN ('pending', 'running', 'completed', 'failed')",
            name=op.f("ck_lap_telemetry_ingestions_status"),
        ),
        sa.CheckConstraint(
            "source IN ('live_signalr', 'fastf1_archive', 'jolpica')",
            name=op.f("ck_lap_telemetry_ingestions_source"),
        ),
        sa.CheckConstraint(
            "record_state IN ('provisional', 'finalized')",
            name=op.f("ck_lap_telemetry_ingestions_record_state"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f(
                "ck_lap_telemetry_ingestions_attempt_count_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "sample_count >= 0",
            name=op.f("ck_lap_telemetry_ingestions_sample_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["lap_id"],
            ["laps.id"],
            name=op.f("fk_lap_telemetry_ingestions_lap_id_laps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "lap_id",
            name=op.f("pk_lap_telemetry_ingestions"),
        ),
    )
    op.create_index(
        op.f(
            "ix_lap_telemetry_ingestions_status_next_retry_at_requested_at"
        ),
        "lap_telemetry_ingestions",
        ["status", "next_retry_at", "requested_at"],
    )
    op.create_index(
        op.f("ix_lap_telemetry_ingestions_heartbeat_at"),
        "lap_telemetry_ingestions",
        ["heartbeat_at"],
    )

    op.create_table(
        "lap_telemetry_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("lap_id", sa.BigInteger(), nullable=False),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("lap_time_us", sa.BigInteger(), nullable=False),
        sa.Column("session_time_us", sa.BigInteger()),
        sa.Column("distance_m", sa.REAL()),
        sa.Column("relative_distance", sa.REAL()),
        sa.Column("speed_kph", sa.REAL()),
        sa.Column("rpm", sa.Integer()),
        sa.Column("gear", sa.SmallInteger()),
        sa.Column("throttle_percent", sa.REAL()),
        sa.Column("brake", sa.Boolean()),
        sa.Column("drs", sa.SmallInteger()),
        sa.Column("x", sa.REAL()),
        sa.Column("y", sa.REAL()),
        sa.Column("z", sa.REAL()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("record_state", sa.Text(), nullable=False),
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
            "source IN ('live_signalr', 'fastf1_archive', 'jolpica')",
            name=op.f("ck_lap_telemetry_samples_source"),
        ),
        sa.CheckConstraint(
            "record_state IN ('provisional', 'finalized')",
            name=op.f("ck_lap_telemetry_samples_record_state"),
        ),
        sa.CheckConstraint(
            "sample_index >= 0",
            name=op.f("ck_lap_telemetry_samples_sample_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "lap_time_us >= 0",
            name=op.f("ck_lap_telemetry_samples_lap_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "session_time_us IS NULL OR session_time_us >= 0",
            name=op.f(
                "ck_lap_telemetry_samples_session_time_us_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "distance_m IS NULL OR distance_m >= 0",
            name=op.f("ck_lap_telemetry_samples_distance_m_nonnegative"),
        ),
        sa.CheckConstraint(
            "relative_distance IS NULL OR "
            "(relative_distance >= 0 AND relative_distance <= 1.01)",
            name=op.f("ck_lap_telemetry_samples_relative_distance_range"),
        ),
        sa.CheckConstraint(
            "speed_kph IS NULL OR speed_kph >= 0",
            name=op.f("ck_lap_telemetry_samples_speed_kph_nonnegative"),
        ),
        sa.CheckConstraint(
            "rpm IS NULL OR rpm >= 0",
            name=op.f("ck_lap_telemetry_samples_rpm_nonnegative"),
        ),
        sa.CheckConstraint(
            "gear IS NULL OR (gear >= 0 AND gear <= 20)",
            name=op.f("ck_lap_telemetry_samples_gear_range"),
        ),
        sa.CheckConstraint(
            "throttle_percent IS NULL OR "
            "(throttle_percent >= 0 AND throttle_percent <= 100)",
            name=op.f("ck_lap_telemetry_samples_throttle_percent_range"),
        ),
        sa.CheckConstraint(
            "drs IS NULL OR (drs >= 0 AND drs <= 20)",
            name=op.f("ck_lap_telemetry_samples_drs_range"),
        ),
        sa.ForeignKeyConstraint(
            ["lap_id"],
            ["laps.id"],
            name=op.f("fk_lap_telemetry_samples_lap_id_laps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_lap_telemetry_samples"),
        ),
        sa.UniqueConstraint(
            "lap_id",
            "sample_index",
            name=op.f(
                "uq_lap_telemetry_samples_lap_id_sample_index"
            ),
        ),
    )
    op.create_index(
        op.f("ix_lap_telemetry_samples_lap_id_sample_index"),
        "lap_telemetry_samples",
        ["lap_id", "sample_index"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_lap_telemetry_samples_lap_id_sample_index"),
        table_name="lap_telemetry_samples",
    )
    op.drop_table("lap_telemetry_samples")
    op.drop_index(
        op.f("ix_lap_telemetry_ingestions_heartbeat_at"),
        table_name="lap_telemetry_ingestions",
    )
    op.drop_index(
        op.f(
            "ix_lap_telemetry_ingestions_status_next_retry_at_requested_at"
        ),
        table_name="lap_telemetry_ingestions",
    )
    op.drop_table("lap_telemetry_ingestions")
    op.execute(
        "DELETE FROM upstream_request_events WHERE operation = 'telemetry'"
    )
    op.drop_constraint(
        op.f("ck_upstream_request_events_operation"),
        "upstream_request_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_upstream_request_events_operation"),
        "upstream_request_events",
        "operation IN ('archive', 'schedule')",
    )
