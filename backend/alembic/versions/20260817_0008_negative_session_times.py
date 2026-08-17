"""Allow session-relative lap instants to be negative.

FastF1 measures a lap's ``Time``, ``LapStartTime``, pit times and sector
session times from the moment a session officially starts. A car already on
track before that start therefore carries a negative one -- in the 2025
Spanish Grand Prix third practice, thirteen out laps do.

Those columns are instants, not durations. Requiring them to be non-negative
rejected the whole session's 312 laps for the sake of thirteen rows, so the
constraints are dropped here. The duration columns beside them --
``lap_time_us`` and the per-sector times -- keep theirs, because no lap can
take a negative amount of time to complete.

Revision ID: 20260817_0008
Revises: 20260728_0007
Create Date: 2026-08-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_0008"
down_revision: str | None = "20260728_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, short constraint name, column) for every instant that may precede
#: t=0. The names are short because the metadata naming convention prefixes
#: them with ``ck_<table>_``; passing a full name here produces ``ck_laps_ck_
#: laps_...``.
_INSTANT_CONSTRAINTS = (
    ("laps", "session_time_us_nonnegative", "session_time_us"),
    ("laps", "lap_start_time_us_nonnegative", "lap_start_time_us"),
    ("laps", "pit_in_time_us_nonnegative", "pit_in_time_us"),
    ("laps", "pit_out_time_us_nonnegative", "pit_out_time_us"),
    (
        "laps",
        "sector_1_session_time_us_nonnegative",
        "sector_1_session_time_us",
    ),
    (
        "laps",
        "sector_2_session_time_us_nonnegative",
        "sector_2_session_time_us",
    ),
    (
        "laps",
        "sector_3_session_time_us_nonnegative",
        "sector_3_session_time_us",
    ),
    (
        "lap_telemetry_samples",
        "session_time_us_nonnegative",
        "session_time_us",
    ),
)


def upgrade() -> None:
    for table, constraint, _ in _INSTANT_CONSTRAINTS:
        op.drop_constraint(constraint, table, type_="check")


def downgrade() -> None:
    # Rows that are now legal would violate the restored constraint, so they
    # are cleared first. Downgrading is lossy by nature here: a negative
    # instant has no non-negative representation, and NULL is the only honest
    # substitute for "before the session started".
    for table, constraint, column in _INSTANT_CONSTRAINTS:
        op.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} < 0")
        op.create_check_constraint(
            constraint,
            table,
            f"{column} IS NULL OR {column} >= 0",
        )
