"""Create the sporting-data schema.

Revision ID: 20260728_0002
Revises: 20260727_0001
Create Date: 2026-07-28

Downgrading this revision removes all driver, entry, result, and lap data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0002"
down_revision: str | None = "20260727_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECORD_STATES = "'provisional', 'finalized'"
SOURCES = "'live_signalr', 'fastf1_archive', 'jolpica'"


def upgrade() -> None:
    op.create_table(
        "drivers",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("jolpica_driver_id", sa.Text(), nullable=True),
        sa.Column("live_reference", sa.Text(), nullable=True),
        sa.Column("given_name", sa.Text(), nullable=True),
        sa.Column("family_name", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drivers")),
        sa.UniqueConstraint(
            "jolpica_driver_id",
            name=op.f("uq_drivers_jolpica_driver_id"),
        ),
        sa.UniqueConstraint(
            "live_reference",
            name=op.f("uq_drivers_live_reference"),
        ),
    )

    op.create_table(
        "session_entries",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("driver_id", sa.BigInteger(), nullable=True),
        sa.Column("entry_key", sa.Text(), nullable=False),
        sa.Column("racing_number", sa.Text(), nullable=True),
        sa.Column("abbreviation", sa.Text(), nullable=True),
        sa.Column("broadcast_name", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("team_jolpica_id", sa.Text(), nullable=True),
        sa.Column("team_name", sa.Text(), nullable=True),
        sa.Column("team_color", sa.Text(), nullable=True),
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
            f"source IN ({SOURCES})",
            name=op.f("ck_session_entries_source"),
        ),
        sa.CheckConstraint(
            f"record_state IN ({RECORD_STATES})",
            name=op.f("ck_session_entries_record_state"),
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["drivers.id"],
            name=op.f("fk_session_entries_driver_id_drivers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_entries_session_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_entries")),
        sa.UniqueConstraint(
            "session_id",
            "entry_key",
            name=op.f("uq_session_entries_session_id_entry_key"),
        ),
    )
    op.create_index(
        "uq_session_entries_session_driver",
        "session_entries",
        ["session_id", "driver_id"],
        unique=True,
        postgresql_where=sa.text("driver_id IS NOT NULL"),
    )
    op.create_index(
        "uq_session_entries_session_racing_number",
        "session_entries",
        ["session_id", "racing_number"],
        unique=True,
        postgresql_where=sa.text("racing_number IS NOT NULL"),
    )

    op.create_table(
        "session_results",
        sa.Column("session_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("classified_position", sa.Text(), nullable=True),
        sa.Column("grid_position", sa.SmallInteger(), nullable=True),
        sa.Column("points", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("laps_completed", sa.SmallInteger(), nullable=True),
        sa.Column("q1_time_us", sa.BigInteger(), nullable=True),
        sa.Column("q2_time_us", sa.BigInteger(), nullable=True),
        sa.Column("q3_time_us", sa.BigInteger(), nullable=True),
        sa.Column("elapsed_time_us", sa.BigInteger(), nullable=True),
        sa.Column("gap_to_leader_us", sa.BigInteger(), nullable=True),
        sa.Column("gap_to_leader_laps", sa.SmallInteger(), nullable=True),
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
            f"source IN ({SOURCES})",
            name=op.f("ck_session_results_source"),
        ),
        sa.CheckConstraint(
            f"record_state IN ({RECORD_STATES})",
            name=op.f("ck_session_results_record_state"),
        ),
        sa.CheckConstraint(
            "position IS NULL OR position >= 1",
            name=op.f("ck_session_results_position_positive"),
        ),
        sa.CheckConstraint(
            "grid_position IS NULL OR grid_position >= 0",
            name=op.f("ck_session_results_grid_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "points IS NULL OR points >= 0",
            name=op.f("ck_session_results_points_nonnegative"),
        ),
        sa.CheckConstraint(
            "laps_completed IS NULL OR laps_completed >= 0",
            name=op.f("ck_session_results_laps_completed_nonnegative"),
        ),
        sa.CheckConstraint(
            "q1_time_us IS NULL OR q1_time_us >= 0",
            name=op.f("ck_session_results_q1_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "q2_time_us IS NULL OR q2_time_us >= 0",
            name=op.f("ck_session_results_q2_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "q3_time_us IS NULL OR q3_time_us >= 0",
            name=op.f("ck_session_results_q3_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "elapsed_time_us IS NULL OR elapsed_time_us >= 0",
            name=op.f("ck_session_results_elapsed_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "gap_to_leader_us IS NULL OR gap_to_leader_us >= 0",
            name=op.f("ck_session_results_gap_to_leader_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "gap_to_leader_laps IS NULL OR gap_to_leader_laps >= 0",
            name=op.f("ck_session_results_gap_to_leader_laps_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["session_entry_id"],
            ["session_entries.id"],
            name=op.f("fk_session_results_session_entry_id_session_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "session_entry_id",
            name=op.f("pk_session_results"),
        ),
    )

    op.create_table(
        "laps",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("session_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("lap_number", sa.SmallInteger(), nullable=False),
        sa.Column("stint_number", sa.SmallInteger(), nullable=True),
        sa.Column("session_time_us", sa.BigInteger(), nullable=True),
        sa.Column("lap_time_us", sa.BigInteger(), nullable=True),
        sa.Column("lap_start_time_us", sa.BigInteger(), nullable=True),
        sa.Column("pit_out_time_us", sa.BigInteger(), nullable=True),
        sa.Column("pit_in_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_1_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_2_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_3_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_1_session_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_2_session_time_us", sa.BigInteger(), nullable=True),
        sa.Column("sector_3_session_time_us", sa.BigInteger(), nullable=True),
        sa.Column("speed_i1_kph", sa.REAL(), nullable=True),
        sa.Column("speed_i2_kph", sa.REAL(), nullable=True),
        sa.Column("speed_fl_kph", sa.REAL(), nullable=True),
        sa.Column("speed_st_kph", sa.REAL(), nullable=True),
        sa.Column("is_personal_best", sa.Boolean(), nullable=False),
        sa.Column("compound", sa.Text(), nullable=True),
        sa.Column("tyre_life_laps", sa.SmallInteger(), nullable=True),
        sa.Column("fresh_tyre", sa.Boolean(), nullable=True),
        sa.Column("track_status", sa.Text(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=True),
        sa.Column("deleted_reason", sa.Text(), nullable=True),
        sa.Column("fastf1_generated", sa.Boolean(), nullable=False),
        sa.Column("is_accurate", sa.Boolean(), nullable=False),
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
            f"source IN ({SOURCES})",
            name=op.f("ck_laps_source"),
        ),
        sa.CheckConstraint(
            f"record_state IN ({RECORD_STATES})",
            name=op.f("ck_laps_record_state"),
        ),
        sa.CheckConstraint(
            "lap_number >= 1",
            name=op.f("ck_laps_lap_number_positive"),
        ),
        sa.CheckConstraint(
            "stint_number IS NULL OR stint_number >= 1",
            name=op.f("ck_laps_stint_number_positive"),
        ),
        sa.CheckConstraint(
            "session_time_us IS NULL OR session_time_us >= 0",
            name=op.f("ck_laps_session_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "lap_time_us IS NULL OR lap_time_us >= 0",
            name=op.f("ck_laps_lap_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "lap_start_time_us IS NULL OR lap_start_time_us >= 0",
            name=op.f("ck_laps_lap_start_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "pit_out_time_us IS NULL OR pit_out_time_us >= 0",
            name=op.f("ck_laps_pit_out_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "pit_in_time_us IS NULL OR pit_in_time_us >= 0",
            name=op.f("ck_laps_pit_in_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_1_time_us IS NULL OR sector_1_time_us >= 0",
            name=op.f("ck_laps_sector_1_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_2_time_us IS NULL OR sector_2_time_us >= 0",
            name=op.f("ck_laps_sector_2_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_3_time_us IS NULL OR sector_3_time_us >= 0",
            name=op.f("ck_laps_sector_3_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_1_session_time_us IS NULL OR sector_1_session_time_us >= 0",
            name=op.f("ck_laps_sector_1_session_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_2_session_time_us IS NULL OR sector_2_session_time_us >= 0",
            name=op.f("ck_laps_sector_2_session_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "sector_3_session_time_us IS NULL OR sector_3_session_time_us >= 0",
            name=op.f("ck_laps_sector_3_session_time_us_nonnegative"),
        ),
        sa.CheckConstraint(
            "speed_i1_kph IS NULL OR speed_i1_kph >= 0",
            name=op.f("ck_laps_speed_i1_kph_nonnegative"),
        ),
        sa.CheckConstraint(
            "speed_i2_kph IS NULL OR speed_i2_kph >= 0",
            name=op.f("ck_laps_speed_i2_kph_nonnegative"),
        ),
        sa.CheckConstraint(
            "speed_fl_kph IS NULL OR speed_fl_kph >= 0",
            name=op.f("ck_laps_speed_fl_kph_nonnegative"),
        ),
        sa.CheckConstraint(
            "speed_st_kph IS NULL OR speed_st_kph >= 0",
            name=op.f("ck_laps_speed_st_kph_nonnegative"),
        ),
        sa.CheckConstraint(
            "tyre_life_laps IS NULL OR tyre_life_laps >= 0",
            name=op.f("ck_laps_tyre_life_laps_nonnegative"),
        ),
        sa.CheckConstraint(
            "position IS NULL OR position >= 1",
            name=op.f("ck_laps_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["session_entry_id"],
            ["session_entries.id"],
            name=op.f("fk_laps_session_entry_id_session_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_laps")),
        sa.UniqueConstraint(
            "session_entry_id",
            "lap_number",
            name=op.f("uq_laps_session_entry_id_lap_number"),
        ),
    )


def downgrade() -> None:
    op.drop_table("laps")
    op.drop_table("session_results")
    op.drop_index(
        "uq_session_entries_session_racing_number",
        table_name="session_entries",
    )
    op.drop_index(
        "uq_session_entries_session_driver",
        table_name="session_entries",
    )
    op.drop_table("session_entries")
    op.drop_table("drivers")
