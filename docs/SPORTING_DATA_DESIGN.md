# Sporting Data Design

Status: **accepted; schema implemented**
Date: **2026-07-28**

This document defines the accepted Revision 2 database model. It is based on direct inspection of representative FastF1 3.8.3 data and the official FastF1 data reference. The SQLAlchemy models, Alembic revision, locked FastF1 runtime dependency, cache-backed one-session loader, pure normalization layer, and atomic one-session archive persistence are implemented. End-to-end backfill and worker execution are not implemented yet.

## Scope

Revision 2 is intended to store normalized driver participation, session results, and lap summaries without storing high-frequency telemetry.

Historical ingestion starts with the 2018 season. Earlier schedules and results are outside the accepted product scope.

Implemented tables:

- `drivers`
- `session_entries`
- `session_results`
- `laps`

Out of scope:

- Car and position telemetry samples.
- Weather, race-control message, track-status event, and session-status event tables.
- Constructor/team history as a separate entity.
- Championship standings.
- FastF1 worker orchestration.
- API response schemas.

## Inspection Baseline

The inspection used FastF1 3.8.3 with its cache enabled. Requests were processed sequentially, and telemetry, weather, and messages were disabled.

Inspected sessions:

| Season | Event | Session | Purpose |
| --- | --- | --- | --- |
| 2024 | Bahrain Grand Prix | Race | Modern results and 1,129 lap rows |
| 2024 | Bahrain Grand Prix | Qualifying | Qualifying result fields |
| 2024 | Miami Grand Prix | Sprint | Sprint result behavior |
| 2018 | Australian Grand Prix | Race | Earliest full timing era |
| 1950 | British Grand Prix | Race | Results-only historical behavior |

Official documentation states that schedules and results are available back to 1950, while detailed timing, telemetry, and session information are generally available from 2018 onward.

References:

- [FastF1 package](https://pypi.org/project/fastf1/)
- [FastF1 data reference](https://docs.fastf1.dev/data_reference/index.html)
- [FastF1 core data objects](https://docs.fastf1.dev/core.html)

## Observed Data Characteristics

### Driver identity

- `SessionResults.DriverId` is the Ergast/Jolpica driver identifier.
- The inspected values remained stable across seasons and team changes. Examples include `hamilton`, `alonso`, `leclerc`, and `max_verstappen`.
- Racing number, abbreviation, display name, and team are attributes of a session entry, not safe global driver identifiers.
- Modern live timing also exposes a `Reference` field, but equivalence between every live reference and Jolpica driver identifier has not been verified.
- Names and racing numbers must never be used to silently merge global driver records.

### Historical shared cars

The 1950 British Grand Prix contains multiple drivers with the same racing number:

- Racing number `10`: Joe Fry and Brian Shawe Taylor.
- Racing number `9`: Tony Rolt and Peter Walker.

These shared-car results are outside the accepted 2018+ ingestion scope. They still demonstrate why a racing number must never be a global driver identifier.

For supported 2018+ data, a non-null racing number is expected to identify at most one entry within a single session. It may be reused by a different driver in a different session or season.

### Racing-number reuse

Racing numbers are stored on `session_entries`, not `drivers`.

For example:

- A 2024 session entry can link `max_verstappen` to racing number `1`.
- A 2026 session entry can link `norris` to racing number `1`.
- The global driver rows remain distinct because their internal IDs and Jolpica driver identifiers differ.

Uniqueness is scoped to `(session_id, racing_number)` for non-null values. No database constraint makes a racing number unique across sessions, events, seasons, or drivers.

### Results

FastF1 always exposes the same result columns, but irrelevant values are null for a given session type.

Important fields:

- Driver and team snapshot fields.
- Finishing and classified position.
- Grid position.
- Q1, Q2, and Q3 times.
- Status, points, and completed laps.
- Race or sprint `Time`.

The observed race `Time` semantics are not uniform:

- The winner has total elapsed race time.
- Same-lap finishers have their time gap to the winner.
- Drivers sufficiently far behind or without a usable time can have null.

Revision 2 stores explicit elapsed-time and gap fields instead of one ambiguous duration.

### Laps

The inspected lap columns contain timing, pit, tyre, speed-trap, position, deletion, and accuracy information. Several float columns are semantically integers because pandas uses floating-point storage to allow null values:

- `LapNumber`
- `Stint`
- `TyreLife`
- `Position`

All non-null inspected values for these fields were integral and should be validated before conversion.

`TrackStatus` is a string that can contain combined status codes such as `12` or `21`; it must not be converted to an integer or narrow enum.

`LapStartDate` was null when telemetry was disabled. Revision 2 stores session-relative lap timing and omits this telemetry-dependent absolute value.

`Deleted` is nullable and depends on race-control messages being loaded. The schema should preserve the unknown state rather than converting null to `false`.

## Implemented Tables

### `drivers`

Stores global driver identity when it can be resolved safely.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGINT IDENTITY` | Primary key. |
| `jolpica_driver_id` | `TEXT` | Nullable unique FastF1 `DriverId`. |
| `live_reference` | `TEXT` | Nullable unique live timing reference; populated only after live identity behavior is verified. |
| `given_name` | `TEXT` | Nullable. |
| `family_name` | `TEXT` | Nullable. |
| `full_name` | `TEXT` | Display name. |
| `country_code` | `TEXT` | Nullable source-provided country code. |
| `created_at` | `TIMESTAMPTZ` | Server default `now()`. |
| `updated_at` | `TIMESTAMPTZ` | Updated by application writes. |

Accepted behavior:

- Upsert archive identities by non-empty `jolpica_driver_id`.
- Do not fall back to names, abbreviations, or racing numbers for global identity matching.
- Do not create a global driver merely to satisfy a missing external identity. An unresolved session entry may temporarily have a null `driver_id`.

### `session_entries`

Stores the identity and team snapshot for one participant in one session.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGINT IDENTITY` | Primary key. |
| `session_id` | `BIGINT` | Foreign key to `sessions.id`, delete restricted. |
| `driver_id` | `BIGINT` | Nullable foreign key to `drivers.id`, delete restricted. |
| `entry_key` | `TEXT` | Deterministic session-local ingestion key. |
| `racing_number` | `TEXT` | Nullable; unique only within one supported session. |
| `abbreviation` | `TEXT` | Nullable three-letter display value. |
| `broadcast_name` | `TEXT` | Nullable source display value. |
| `display_name` | `TEXT` | Non-null participant name snapshot. |
| `team_jolpica_id` | `TEXT` | Nullable constructor identifier. |
| `team_name` | `TEXT` | Nullable session snapshot. |
| `team_color` | `TEXT` | Nullable source color value. |
| `source` | `TEXT` | Checked source value. |
| `record_state` | `TEXT` | `provisional` or `finalized`. |
| `created_at` | `TIMESTAMPTZ` | Server default `now()`. |
| `updated_at` | `TIMESTAMPTZ` | Updated by application writes. |

Constraints and indexes:

- Unique `(session_id, entry_key)`.
- Partial unique index `(session_id, racing_number)` where `racing_number IS NOT NULL`.
- Partial unique index `(session_id, driver_id)` where `driver_id IS NOT NULL`.
- Named checks for `source` and `record_state`.

The ingestion layer defines `entry_key`. For FastF1 archive data, a namespaced Jolpica driver identifier is the preferred key. A deterministic fallback may be used only for the session entry and must not create or merge a global driver identity.

### `session_results`

Stores one normalized result row for a session entry.

| Column | Type | Notes |
| --- | --- | --- |
| `session_entry_id` | `BIGINT` | Primary key and foreign key to `session_entries.id`. |
| `position` | `SMALLINT` | Nullable final position. |
| `classified_position` | `TEXT` | Nullable official value such as `1`, `R`, `D`, or `N`. |
| `grid_position` | `SMALLINT` | Nullable; zero remains valid for pit-lane starts. |
| `points` | `NUMERIC(7,3)` | Nullable exact points value. |
| `status` | `TEXT` | Nullable source status. |
| `laps_completed` | `SMALLINT` | Nullable. |
| `q1_time_us` | `BIGINT` | Nullable non-negative duration. |
| `q2_time_us` | `BIGINT` | Nullable non-negative duration. |
| `q3_time_us` | `BIGINT` | Nullable non-negative duration. |
| `elapsed_time_us` | `BIGINT` | Nullable normalized total elapsed race/sprint time. |
| `gap_to_leader_us` | `BIGINT` | Nullable non-negative same-lap gap; zero for the winner. |
| `gap_to_leader_laps` | `SMALLINT` | Nullable non-negative lap deficit. |
| `source` | `TEXT` | Checked source value. |
| `record_state` | `TEXT` | `provisional` or `finalized`. |
| `created_at` | `TIMESTAMPTZ` | Server default `now()`. |
| `updated_at` | `TIMESTAMPTZ` | Updated by application writes. |

Named checks should reject negative positions, durations, lap counts, gaps, and points while allowing nulls. The ingestion layer must explicitly normalize FastF1’s mixed `Time` semantics.

### `laps`

Stores one normalized lap summary. It does not contain raw telemetry samples.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGINT IDENTITY` | Primary key. |
| `session_entry_id` | `BIGINT` | Foreign key to `session_entries.id`, delete restricted. |
| `lap_number` | `SMALLINT` | Validated integral lap number. |
| `stint_number` | `SMALLINT` | Nullable validated integral stint. |
| `session_time_us` | `BIGINT` | Nullable lap-end session time. |
| `lap_time_us` | `BIGINT` | Nullable lap duration. |
| `lap_start_time_us` | `BIGINT` | Nullable session-relative lap start. |
| `pit_out_time_us` | `BIGINT` | Nullable session-relative pit exit. |
| `pit_in_time_us` | `BIGINT` | Nullable session-relative pit entry. |
| `sector_1_time_us` | `BIGINT` | Nullable duration. |
| `sector_2_time_us` | `BIGINT` | Nullable duration. |
| `sector_3_time_us` | `BIGINT` | Nullable duration. |
| `sector_1_session_time_us` | `BIGINT` | Nullable session-relative time. |
| `sector_2_session_time_us` | `BIGINT` | Nullable session-relative time. |
| `sector_3_session_time_us` | `BIGINT` | Nullable session-relative time. |
| `speed_i1_kph` | `REAL` | Nullable intermediate speed. |
| `speed_i2_kph` | `REAL` | Nullable intermediate speed. |
| `speed_fl_kph` | `REAL` | Nullable finish-line speed. |
| `speed_st_kph` | `REAL` | Nullable speed-trap speed. |
| `is_personal_best` | `BOOLEAN` | Non-null. |
| `compound` | `TEXT` | Nullable tyre compound. |
| `tyre_life_laps` | `SMALLINT` | Nullable validated integral tyre age. |
| `fresh_tyre` | `BOOLEAN` | Nullable source value. |
| `track_status` | `TEXT` | Nullable combined status string. |
| `position` | `SMALLINT` | Nullable position at lap end. |
| `deleted` | `BOOLEAN` | Nullable; null means unknown. |
| `deleted_reason` | `TEXT` | Nullable. |
| `fastf1_generated` | `BOOLEAN` | Non-null. |
| `is_accurate` | `BOOLEAN` | Non-null. |
| `source` | `TEXT` | Checked source value. |
| `record_state` | `TEXT` | `provisional` or `finalized`. |
| `created_at` | `TIMESTAMPTZ` | Server default `now()`. |
| `updated_at` | `TIMESTAMPTZ` | Updated by application writes. |

Constraints and indexes:

- Unique `(session_entry_id, lap_number)`.
- Named non-negative checks for integral counters, durations, and speeds.
- Named checks for `source` and `record_state`.

The unique constraint already provides the primary driver/lap lookup index; a duplicate plain index on the same columns is unnecessary.

## Normalization Rules

- Convert pandas null, `NaN`, and `NaT` values to SQL `NULL`.
- Convert pandas timedeltas to integer microseconds without passing floating-point seconds through the conversion.
- Reject non-integral values before converting lap numbers, stints, tyre life, positions, or completed laps to integers.
- Preserve `classified_position` and `track_status` as text.
- Preserve nullable booleans such as `Deleted`; do not coerce unknown to `false`.
- Normalize source colors before API exposure, but do not impose a database format until historical values are sampled more broadly.
- Mark FastF1 archive rows as `fastf1_archive` and normally `finalized`.
- Write one session’s entries, results, and laps in one transaction.

The pure transformation and validation rules are implemented in
`backend/app/ingestion/fastf1_normalization.py`.
Cache-backed one-session loading is implemented in
`backend/app/ingestion/fastf1_loader.py`.
Atomic archive snapshot persistence is implemented in
`backend/app/ingestion/archive_persistence.py`.

## Idempotency

- Upsert a driver only by a verified external identity.
- Upsert a session entry by `(session_id, entry_key)`.
- Upsert a result by `session_entry_id`.
- Upsert a lap by `(session_entry_id, lap_number)`.
- Reprocessing must update owned rows rather than blindly append duplicates.
- Remove stale archive-owned rows only under the accepted atomic replacement
  contract in `docs/FASTF1_INGESTION_CONTRACT.md`.

## Accepted Design Decisions

1. Unresolved driver identity:
   - Allow nullable `session_entries.driver_id`.
   - Use a deterministic session-local `entry_key`.
   - Never globally merge drivers by name, abbreviation, or racing number.
2. Result timing:
   - Store normalized `elapsed_time_us`, `gap_to_leader_us`, and `gap_to_leader_laps`.
   - Do not persist FastF1’s mixed `Time` semantics in one ambiguous field.
3. Lap summary breadth:
   - Retain speed-trap and data-quality fields because they are low-volume lap summaries, not telemetry.
4. Deleted lap accuracy:
   - Load race-control messages for 2018+ sporting backfills so deleted-lap fields can be populated.
   - Continue to disable telemetry and weather during the sporting-data backfill.

The fourth decision is an accepted ingestion requirement. It is not implemented by the database migration itself.

## Acceptance Criteria for Revision 2

All schema-level criteria below were verified on 2026-07-28 against an isolated PostgreSQL 17 Compose instance:

- Upgrade and downgrade succeed against PostgreSQL 17.
- Alembic reports no model/schema drift.
- Duplicate `(session_id, entry_key)` rows are rejected.
- Duplicate `(session_entry_id, lap_number)` rows are rejected.
- Duplicate non-null racing numbers within one supported session are rejected.
- The same racing number can be used by different drivers in different sessions or seasons.
- Invalid source, record state, negative durations, and negative counters are rejected.
- Null optional result and lap fields are accepted.
- A session from the accepted 2018+ range can store results and laps without telemetry.
- Re-ingesting the same normalized session is idempotent.
