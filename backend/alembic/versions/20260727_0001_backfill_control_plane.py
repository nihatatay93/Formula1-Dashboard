"""Create the backfill control-plane schema.

Revision ID: 20260727_0001
Revises:
Create Date: 2026-07-27

Downgrading this revision removes all control-plane data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INGESTION_STATUSES = "'pending', 'running', 'completed', 'failed'"
RECORD_STATES = "'provisional', 'finalized'"
REQUEST_REASONS = "'missing', 'partial', 'stale', 'manual'"
SOURCES = "'live_signalr', 'fastf1_archive', 'jolpica'"


def upgrade() -> None:
    op.create_table(
        "seasons",
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("coverage_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_valid_until", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("year >= 1950", name=op.f("ck_seasons_year_minimum")),
        sa.PrimaryKeyConstraint("year", name=op.f("pk_seasons")),
    )

    op.create_table(
        "events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("season_year", sa.SmallInteger(), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("official_name", sa.Text(), nullable=True),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("event_format", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
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
            f"source IN ({SOURCES})",
            name=op.f("ck_events_source"),
        ),
        sa.ForeignKeyConstraint(
            ["season_year"],
            ["seasons.year"],
            name=op.f("fk_events_season_year_seasons"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint(
            "season_year",
            "round_number",
            name=op.f("uq_events_season_year_round_number"),
        ),
    )
    op.create_index(
        "ix_events_season_year_starts_at",
        "events",
        ["season_year", "starts_at"],
        unique=False,
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("session_name", sa.Text(), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
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
            f"source IN ({SOURCES})",
            name=op.f("ck_sessions_source"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_sessions_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint(
            "event_id",
            "session_key",
            name=op.f("uq_sessions_event_id_session_key"),
        ),
    )
    op.create_index(
        "ix_sessions_event_id_scheduled_start_at",
        "sessions",
        ["event_id", "scheduled_start_at"],
        unique=False,
    )

    op.create_table(
        "session_ingestions",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
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
        sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
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
            f"status IN ({INGESTION_STATUSES})",
            name=op.f("ck_session_ingestions_status"),
        ),
        sa.CheckConstraint(
            f"source IN ({SOURCES})",
            name=op.f("ck_session_ingestions_source"),
        ),
        sa.CheckConstraint(
            f"record_state IN ({RECORD_STATES})",
            name=op.f("ck_session_ingestions_record_state"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_session_ingestions_attempt_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_ingestions_session_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "session_id",
            name=op.f("pk_session_ingestions"),
        ),
    )
    op.create_index(
        "ix_session_ingestions_status_next_retry_at",
        "session_ingestions",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_session_ingestions_heartbeat_at",
        "session_ingestions",
        ["heartbeat_at"],
        unique=False,
    )

    op.create_table(
        "backfill_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season_year", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            f"status IN ({INGESTION_STATUSES})",
            name=op.f("ck_backfill_jobs_status"),
        ),
        sa.CheckConstraint(
            f"request_reason IN ({REQUEST_REASONS})",
            name=op.f("ck_backfill_jobs_request_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["season_year"],
            ["seasons.year"],
            name=op.f("fk_backfill_jobs_season_year_seasons"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backfill_jobs")),
    )
    op.create_index(
        "ix_backfill_jobs_status_requested_at",
        "backfill_jobs",
        ["status", "requested_at"],
        unique=False,
    )
    op.create_index(
        "uq_backfill_jobs_active_season",
        "backfill_jobs",
        ["season_year"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "backfill_job_sessions",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            f"status IN ({INGESTION_STATUSES})",
            name=op.f("ck_backfill_job_sessions_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_backfill_job_sessions_attempt_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["backfill_jobs.id"],
            name=op.f("fk_backfill_job_sessions_job_id_backfill_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_backfill_job_sessions_session_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "job_id",
            "session_id",
            name=op.f("pk_backfill_job_sessions"),
        ),
    )
    op.create_index(
        "ix_backfill_job_sessions_status_next_retry_at_queued_at",
        "backfill_job_sessions",
        ["status", "next_retry_at", "queued_at"],
        unique=False,
    )
    op.create_index(
        "ix_backfill_job_sessions_job_id_status",
        "backfill_job_sessions",
        ["job_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backfill_job_sessions_job_id_status",
        table_name="backfill_job_sessions",
    )
    op.drop_index(
        "ix_backfill_job_sessions_status_next_retry_at_queued_at",
        table_name="backfill_job_sessions",
    )
    op.drop_table("backfill_job_sessions")

    op.drop_index("uq_backfill_jobs_active_season", table_name="backfill_jobs")
    op.drop_index("ix_backfill_jobs_status_requested_at", table_name="backfill_jobs")
    op.drop_table("backfill_jobs")

    op.drop_index(
        "ix_session_ingestions_heartbeat_at",
        table_name="session_ingestions",
    )
    op.drop_index(
        "ix_session_ingestions_status_next_retry_at",
        table_name="session_ingestions",
    )
    op.drop_table("session_ingestions")

    op.drop_index(
        "ix_sessions_event_id_scheduled_start_at",
        table_name="sessions",
    )
    op.drop_table("sessions")

    op.drop_index(
        "ix_events_season_year_starts_at",
        table_name="events",
    )
    op.drop_table("events")
    op.drop_table("seasons")
