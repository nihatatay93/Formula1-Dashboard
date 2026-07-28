# Historical Session Read API Design

Status: **accepted and implemented through the base dashboard**
Date: **2026-07-28**

Strict Pydantic response and lap-query models are implemented in
`backend/app/api/contracts.py`, with focused validation and serialization
coverage in `backend/tests/test_historical_session_contracts.py`. Repeatable-read,
read-only PostgreSQL services are implemented in
`backend/app/api/session_data.py`, with integration coverage in
`backend/tests/test_session_data.py`. Thin HTTP routes, stable error mappings,
query/path validation, no-store headers, and generated OpenAPI paths are
implemented in `backend/app/api/sessions.py`, with endpoint coverage in
`backend/tests/test_session_endpoints.py`. The base season-to-session
navigation, result table, participant selection, loaded-lap visualization,
detailed lap table, and keyset pagination are implemented in
`frontend/src/SessionExplorer.tsx`.

## Purpose

This document defines the accepted next historical REST API slice: read-only
session metadata, session entries/results, and paginated lap summaries.

The slice builds on the implemented season overview and sporting-data schema.
It does not add telemetry, standings, search, live timing, or any write
operation.

The design must:

- Let web and future iOS clients open a session selected from a season
  overview.
- Keep session metadata, grid-sized result data, and lap summaries separate.
- Use a session entry, not a racing number, as the participant identity inside
  a session.
- Distinguish an existing session with unavailable data from an available
  snapshot that legitimately contains null fields.
- Continue serving a preserved completed snapshot while a correction attempt is
  pending, running, or failed.
- Keep lap responses bounded and deterministically ordered.
- Avoid exposing internal errors, raw upstream payloads, or ingestion-only
  identity keys.

## Existing Contract Conventions

The accepted endpoints retain the implemented historical API conventions:

- Prefix all routes with `/api/v1`.
- Use `snake_case` JSON fields.
- Serialize PostgreSQL `BIGINT` identifiers as canonical positive decimal
  strings.
- Serialize timestamps as ISO 8601 UTC values.
- Return exact `NUMERIC` values, such as points, as decimal strings.
- Return normalized microsecond durations as JSON integers. Formula 1 session
  durations remain safely within JavaScript's exact integer range.
- Preserve unknown source values as `null`; do not coerce them to zero, an empty
  string, or `false`.
- Use strict response models and include them in generated OpenAPI.
- Use `Cache-Control: no-store` because an archive snapshot can be corrected.
- Read each response inside one PostgreSQL `REPEATABLE READ, READ ONLY`
  transaction.
- Return only stable, client-safe error codes and messages.

Cross-endpoint snapshot consistency is not promised. Each request is internally
consistent, and each sporting response includes the completed snapshot
timestamp so clients can detect that a newer archive snapshot was installed
between requests.

## Resource Identity

The URL-visible identifiers are internal database identifiers:

- `session_id` identifies one row in `sessions`.
- `session_entry_id` identifies one participant snapshot in that session.
- `driver_id`, when present, identifies one resolved global driver.

Racing number is display data. It is unique only within a supported session and
may belong to different drivers in different sessions or seasons. It must never
be accepted as the primary selector for results or laps.

The ingestion-only `session_entries.entry_key` is deliberately not exposed. It
is an implementation detail used for idempotent archive replacement, not a
public client identifier.

## Shared Snapshot State

All three responses use the same snapshot-state shape:

```json
{
  "data_available": true,
  "source": "fastf1_archive",
  "record_state": "finalized",
  "completed_at": "2026-07-28T12:00:00Z",
  "source_updated_at": "2026-07-28T11:59:58Z"
}
```

Rules:

- `data_available` is true when `session_ingestions.completed_at` is non-null.
- A later failed correction does not make an older completed snapshot
  unavailable.
- `source`, `record_state`, `completed_at`, and `source_updated_at` are null
  when no completed snapshot exists.
- The current ingestion lifecycle is returned separately on session detail so
  clients can show pending, running, or failed correction state without
  confusing it with snapshot availability.

## Endpoint 1: Read Session Detail

### `GET /api/v1/sessions/{session_id}`

This endpoint returns bounded session and event metadata plus availability and
row counts. It does not return entries, results, laps, or telemetry.

An existing scheduled session returns `200 OK` even when no sporting snapshot
is available. This makes future, grace-period, and not-yet-ingested sessions
inspectable without representing them as missing resources.

Accepted response:

```json
{
  "id": "210",
  "session_key": "race",
  "session_name": "Race",
  "scheduled_start_at": "2024-03-02T15:00:00Z",
  "scheduled_end_at": "2024-03-02T17:00:00Z",
  "event": {
    "id": "42",
    "season_year": 2024,
    "round_number": 1,
    "official_name": "FORMULA 1 ...",
    "event_name": "Bahrain Grand Prix",
    "country": "Bahrain",
    "location": "Sakhir",
    "event_format": "conventional"
  },
  "snapshot": {
    "data_available": true,
    "source": "fastf1_archive",
    "record_state": "finalized",
    "completed_at": "2026-07-28T12:00:00Z",
    "source_updated_at": "2026-07-28T11:59:58Z"
  },
  "ingestion": {
    "status": "completed",
    "source": "fastf1_archive",
    "record_state": "finalized",
    "attempt_count": 1,
    "completed_at": "2026-07-28T12:00:00Z",
    "next_retry_at": null,
    "last_error": null
  },
  "counts": {
    "entries": 20,
    "results": 20,
    "laps": 1124
  }
}
```

Count rules:

- Counts describe rows in the currently stored snapshot.
- Counts are zero when no snapshot exists.
- A mismatch between entry and result counts is observable rather than hidden.
  The result endpoint can therefore preserve entries whose result is currently
  absent.

The session detail query may read sessions that are no longer part of the
latest schedule-discovery membership. A valid internal session identifier
continues to identify the preserved row. Latest-membership filtering remains a
season-overview concern.

## Endpoint 2: Read Session Entries and Results

### `GET /api/v1/sessions/{session_id}/results`

The result set is not paginated. A Formula 1 session has a bounded, grid-sized
participant set, and pagination would complicate classification display without
materially bounding database or payload cost.

The endpoint returns every stored session entry. `result` is nullable so a
participant snapshot is not silently discarded if a provisional or incomplete
source has not supplied its result yet.

Accepted response:

```json
{
  "session_id": "210",
  "snapshot": {
    "data_available": true,
    "source": "fastf1_archive",
    "record_state": "finalized",
    "completed_at": "2026-07-28T12:00:00Z",
    "source_updated_at": "2026-07-28T11:59:58Z"
  },
  "items": [
    {
      "session_entry_id": "1001",
      "driver": {
        "id": "7",
        "jolpica_driver_id": "max_verstappen",
        "given_name": "Max",
        "family_name": "Verstappen",
        "full_name": "Max Verstappen",
        "country_code": "NED"
      },
      "racing_number": "1",
      "abbreviation": "VER",
      "broadcast_name": "M VERSTAPPEN",
      "display_name": "Max Verstappen",
      "team_jolpica_id": "red_bull",
      "team_name": "Red Bull Racing",
      "team_color_hex": "#3671C6",
      "source": "fastf1_archive",
      "record_state": "finalized",
      "result": {
        "position": 1,
        "classified_position": "1",
        "grid_position": 1,
        "points": "26.000",
        "status": "Finished",
        "laps_completed": 57,
        "q1_time_us": null,
        "q2_time_us": null,
        "q3_time_us": null,
        "elapsed_time_us": 5504742000,
        "gap_to_leader_us": 0,
        "gap_to_leader_laps": 0,
        "source": "fastf1_archive",
        "record_state": "finalized"
      }
    }
  ]
}
```

Driver rules:

- `driver` is null when the session entry has no safely resolved global
  identity.
- A null driver does not make the entry or its result unavailable.
- Names, team, abbreviation, and racing number are session snapshots from the
  entry, not current global-driver attributes.
- `team_color_hex` is a normalized nullable CSS color in `#RRGGBB` form. A
  missing or invalid stored color maps to null.

Ordering is fixed and cannot be selected by the client in this first slice:

1. Non-null `result.position` ascending.
2. Null `result.position` after positioned entries.
3. `session_entry_id` ascending as the deterministic tie-breaker.

This ordering works for race, sprint, qualifying, and practice result snapshots
without attempting to reinterpret source classification text.

## Endpoint 3: Read Lap Summaries for One Session Entry

### `GET /api/v1/sessions/{session_id}/entries/{session_entry_id}/laps`

The nested path makes the session-entry ownership check explicit. It prevents a
client from accidentally combining a session selected in the UI with an entry
from another session.

Query parameters:

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `after_lap` | integer or null | null | Exclusive keyset cursor; must be at least zero. |
| `limit` | integer | `50` | Must be between `1` and `100`. |
| `lap_from` | integer or null | null | Inclusive lower bound; must be at least one. |
| `lap_to` | integer or null | null | Inclusive upper bound; must be at least one. |
| `stint_number` | integer or null | null | Exact stint; must be at least one. |
| `include_deleted` | boolean | `true` | When false, excludes only rows where `deleted` is explicitly true. |

`lap_from` must not be greater than `lap_to`. When `after_lap` and `lap_from`
are both supplied, a row must satisfy both conditions. Null `deleted` values
remain included because null means unknown, not deleted.

Items are always ordered by `lap_number` ascending. Pagination is keyset-based
on the unique `(session_entry_id, lap_number)` database key; it never uses
offset pagination.

Accepted response:

```json
{
  "session_id": "210",
  "session_entry_id": "1001",
  "snapshot": {
    "data_available": true,
    "source": "fastf1_archive",
    "record_state": "finalized",
    "completed_at": "2026-07-28T12:00:00Z",
    "source_updated_at": "2026-07-28T11:59:58Z"
  },
  "filters": {
    "lap_from": null,
    "lap_to": null,
    "stint_number": null,
    "include_deleted": true
  },
  "page": {
    "limit": 50,
    "has_more": true,
    "next_after_lap": 50
  },
  "items": [
    {
      "id": "9001",
      "lap_number": 1,
      "stint_number": 1,
      "session_time_us": 96345123,
      "lap_time_us": 96543210,
      "lap_start_time_us": 0,
      "pit_out_time_us": null,
      "pit_in_time_us": null,
      "sector_1_time_us": 31000123,
      "sector_2_time_us": 42000456,
      "sector_3_time_us": 23542631,
      "sector_1_session_time_us": 31000123,
      "sector_2_session_time_us": 73000579,
      "sector_3_session_time_us": 96543210,
      "speed_i1_kph": 284.1,
      "speed_i2_kph": 301.8,
      "speed_fl_kph": 276.4,
      "speed_st_kph": 319.2,
      "is_personal_best": false,
      "compound": "SOFT",
      "tyre_life_laps": 1,
      "fresh_tyre": true,
      "track_status": "1",
      "position": 1,
      "deleted": false,
      "deleted_reason": null,
      "fastf1_generated": false,
      "is_accurate": true,
      "source": "fastf1_archive",
      "record_state": "finalized"
    }
  ]
}
```

Pagination rules:

- The service requests `limit + 1` rows to determine `has_more`.
- At most `limit` rows are returned.
- `next_after_lap` is the last returned lap number when `has_more` is true;
  otherwise it is null.
- An empty page returns `items: []`, `has_more: false`, and
  `next_after_lap: null`.
- Repeating a request against the same stored snapshot produces the same page.
- If archive replacement occurs between requests, `snapshot.completed_at`
  changes. Clients that require one-version traversal should restart from the
  first page when that value changes.

Lap summaries are relational sporting data, not telemetry. No car-data or
position-data sample arrays are included.

## Future Post-Session Analysis Compatibility

The accepted lap contract supports manual race-pace analysis without changing
the first implementation slice.

A web or iOS client will be able to:

1. Load the session results and select one or more session entries.
2. Load every lap-summary page for each selected entry.
3. Inspect stint, compound, tyre life, pit markers, track status, lap deletion,
   accuracy, lap time, and sector times.
4. Manually select the laps that appear to belong to a representative long run.
5. Calculate an arithmetic mean from the selected non-null `lap_time_us`
   values and compare drivers or teams.

The individual laps remain visible alongside any aggregate. This prevents an
average from hiding the exact manual selection or silently including an
invalid, pit, warm-up, cool-down, traffic-affected, or disrupted lap.

The client should associate a manual selection with:

- `session_id`
- `session_entry_id`
- selected `lap_number` values
- the response `snapshot.completed_at`

The snapshot timestamp matters because a later FastF1 correction can replace
lap values. If it changes, a client must identify the saved selection as based
on an older snapshot and ask the user to review or recalculate it.

The first endpoint implementation will expose source data only. It will not:

- Automatically classify a lap or stint as a race simulation.
- Persist user selections or named analyses.
- Return server-calculated pace aggregates or comparisons.
- Claim that a selected average predicts race pace exactly.

Automatic race-run detection remains a separate future design problem. The
stored lap summaries do not contain verified fuel load, engine mode, run plan,
or driver instructions. Weather is also not stored in the current sporting
schema. Tyre, stint, timing, pit, track-status, and quality fields support useful
analysis, but any inferred race-simulation label must clearly describe its
assumptions.

If analysis later needs to be saved, shared between web and iOS, or reproduced
after a snapshot correction, a backend analysis contract and persistence model
should be designed explicitly. That work must not overload the read-only lap
endpoint or silently store client state.

## Data Availability Semantics

The session-detail endpoint always describes an existing session and returns
its current availability.

The result and lap endpoints require a completed snapshot:

- If `snapshot.data_available` is true, serve the stored snapshot even when the
  latest correction attempt is pending, running, or failed.
- If the session exists but no completed snapshot is stored, return
  `409 session_data_unavailable`.
- Do not return `200` with an empty item list for an unavailable snapshot,
  because that would be indistinguishable from an available snapshot with no
  matching rows.
- Filtered lap queries can legitimately return `200` with an empty item list
  after availability has been established.

No read endpoint starts, retries, or modifies a backfill job.

## Error Contract

All failures use the existing envelope:

```json
{
  "detail": {
    "code": "session_data_unavailable",
    "message": "Historical data is not available for this session."
  }
}
```

Accepted mappings:

| Condition | Status | Stable code |
| --- | ---: | --- |
| Malformed or non-positive path/query integer | `422` | FastAPI validation response |
| `lap_from` is greater than `lap_to` | `422` | `invalid_lap_range` |
| Unknown session | `404` | `session_not_found` |
| Unknown entry or entry outside the requested session | `404` | `session_entry_not_found` |
| Existing session has no completed snapshot | `409` | `session_data_unavailable` |
| Database is unavailable | `503` | `database_unavailable` |
| Database/runtime configuration is invalid | `500` | `server_configuration_error` |

The entry-not-found response is identical for an unknown entry and an entry
owned by another session. It does not disclose cross-session membership.

## Query and Index Plan

No migration is required for this slice.

- Session detail uses the primary session key, joins its event and optional
  ingestion row, and aggregates entry, result, and lap counts.
- Results filter entries by `session_id`, left join global drivers and results,
  and use position plus entry ID ordering.
- Laps first verify the entry belongs to the session, then use the existing
  unique `(session_entry_id, lap_number)` index for filters, ordering, and
  keyset pagination.

The implementation should use explicit projections instead of loading
unbounded ORM relationship graphs.

## Accepted Decisions

1. Use three endpoints instead of embedding entries, results, or laps in the
   session detail.
2. Return all entries/results without pagination because the participant set is
   naturally bounded.
3. Scope lap summaries to one session entry and paginate by the unique lap
   number with a default of 50 and maximum of 100 rows.
4. Include deleted laps by default and preserve unknown deletion state.
5. Return `409 session_data_unavailable` for an existing session without a
   completed snapshot.
6. Serve preserved completed data during later correction attempts and expose
   the completed snapshot timestamp in every sporting response.
7. Expose session-local entry IDs and optional global driver IDs; never use
   racing number as identity.
8. Add no database migration or new package dependency for this slice.

All decisions were explicitly accepted on 2026-07-28.

## Implementation Sequence

1. Implemented: strict Pydantic response/query contracts and focused contract
   tests. Route-level OpenAPI coverage remains with the HTTP boundary.
2. Implemented: read-only session-detail, results, and lap-summary query
   services with PostgreSQL integration tests.
3. Implemented: thin FastAPI routes with path/query validation, no-store
   headers, stable sanitized errors, and OpenAPI response/query documentation.
4. Implemented: verified Ruff and all 379 backend tests against an isolated
   PostgreSQL 17 database, then removed the temporary database.
5. Implemented after a separate review: connected dashboard session
   navigation, result tables, and paginated lap-summary visualizations to only
   these endpoints. Manual lap selection and analysis remain separate future
   work.
