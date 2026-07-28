# Historical Season and Backfill REST API Design

Status: **accepted; API foundation and season overview read service implemented,
endpoints not implemented**
Date: **2026-07-28**

## Purpose

This document proposes the first historical REST API slice. It exposes the
implemented season planner and job progress without yet adding sporting results,
lap pagination, telemetry, WebSocket behavior, authentication, or manual
cancellation.

The first slice must:

- Let web and future iOS clients select a supported season.
- Refresh missing or stale schedule coverage and create or reuse background work.
- Keep command side effects out of read-only endpoints.
- Expose derived season coverage and ingestion state.
- Expose detailed job progress with only sanitized diagnostics.
- Keep completed session availability visible while other sessions are running.

## API Conventions

Accepted conventions:

- Historical product endpoints use the `/api/v1` prefix.
- Existing liveness and readiness endpoints remain unversioned under
  `/api/health`.
- JSON field names use `snake_case`.
- All timestamps are nullable ISO 8601 UTC strings.
- PostgreSQL `BIGINT` identifiers are serialized as decimal strings so JavaScript
  clients never lose integer precision.
- UUID job identifiers are canonical UUID strings.
- Year, round, attempt, and count values remain JSON integers.
- Mutable season and job responses use `Cache-Control: no-store`.
- FastAPI-generated OpenAPI is part of the contract and receives schema tests.
- Error responses expose stable codes and fixed client-safe messages, never raw
  exceptions or upstream payloads.

## Endpoint 1: Ensure Season Backfill

### `POST /api/v1/seasons/{season_year}/backfill`

This is the only endpoint in the first slice that invokes schedule discovery and
the implemented `ensure_season_backfill` planner. It has no request body.

The command is idempotent by season:

- Fresh coverage skips the upstream schedule request.
- Concurrent requests converge on one active job.
- Existing active work is returned rather than duplicated.
- Only missing eligible child rows are appended.
- No job is created when no session is currently archive eligible.

Supported years are from 2018 through the current UTC calendar year.

Accepted response behavior:

- `202 Accepted` when an active job exists after planning, whether newly created
  or reused.
- `200 OK` when planning completes and no job is required.
- A `202` response includes:
  - `Location: /api/v1/backfill-jobs/{job_id}`
  - `Retry-After: 2`

Accepted response:

```json
{
  "season_year": 2024,
  "action": "job_created",
  "coverage": {
    "refresh_reason": "missing",
    "refreshed": true,
    "checked_at": "2026-07-28T12:00:00Z",
    "valid_until": "2026-08-27T12:00:00Z"
  },
  "job": {
    "id": "3e18c9fd-a8eb-458f-b317-55867afdc53f",
    "status": "pending"
  },
  "eligible_session_count": 72,
  "newly_queued_session_count": 72
}
```

`action` is one of:

- `job_created`
- `job_reused`
- `coverage_refreshed`
- `no_action`

The schedule check is intentionally synchronous for this first slice; the
expensive per-session FastF1 work remains in the worker. Schedule latency will be
measured before introducing another background control plane.

## Endpoint 2: Read Season Overview

### `GET /api/v1/seasons/{season_year}`

This endpoint is strictly read-only. It never contacts FastF1 and never creates
or modifies a job.

The database read service behind this future endpoint is implemented. The route
itself is not yet mounted.

For a supported year with no stored coverage, it returns `200 OK` with
`status: "missing"` and an empty event list. `missing` is an accepted domain
state, so absence is represented as state rather than an HTTP `404`.

The response contains:

- Season year and derived status.
- Coverage timestamps and freshness.
- Aggregate session counts.
- Active job summary when present.
- Events and sessions from only the latest successful discovery snapshot.
- Per-session archive eligibility and persistent ingestion state.
- `data_available`, which remains true when a failed correction attempt preserves
  an older successful snapshot.

Accepted shape:

```json
{
  "year": 2024,
  "status": "partial",
  "coverage": {
    "checked_at": "2026-07-28T12:00:00Z",
    "valid_until": "2026-08-27T12:00:00Z",
    "is_stale": false
  },
  "counts": {
    "events": 24,
    "sessions": 120,
    "archive_eligible": 72,
    "data_available": 18,
    "pending": 53,
    "running": 1,
    "completed": 18,
    "failed": 0
  },
  "active_job": {
    "id": "3e18c9fd-a8eb-458f-b317-55867afdc53f",
    "status": "running"
  },
  "events": [
    {
      "id": "42",
      "round_number": 1,
      "official_name": "FORMULA 1 ...",
      "event_name": "Bahrain Grand Prix",
      "country": "Bahrain",
      "location": "Sakhir",
      "event_format": "conventional",
      "starts_at": "2024-02-29T11:30:00Z",
      "ends_at": "2024-03-02T17:00:00Z",
      "sessions": [
        {
          "id": "210",
          "session_key": "race",
          "session_name": "Race",
          "scheduled_start_at": "2024-03-02T15:00:00Z",
          "scheduled_end_at": "2024-03-02T17:00:00Z",
          "archive_eligibility": {
            "eligible": true,
            "reason": "initial_archive",
            "eligible_at": "2024-03-02T19:00:00Z"
          },
          "ingestion": {
            "status": "running",
            "record_state": "finalized",
            "attempt_count": 1,
            "completed_at": null,
            "next_retry_at": null,
            "last_error": null
          },
          "data_available": false
        }
      ]
    }
  ]
}
```

Session ingestion is `null` when no persistent attempt exists. Only fixed,
sanitized `last_error` code/message values may be exposed.

## Derived Season Status

Season status is calculated at read time from the latest discovery membership,
coverage freshness, active job, archive eligibility, and persistent ingestion
state. It describes current archive coverage, not whether the Formula 1
championship itself has ended.

Accepted precedence:

1. `partial`: at least one session has usable completed data and another
   currently required session is pending, running, failed, or due for refresh.
2. `running`: no session has usable completed data and at least one required
   session is running.
3. `pending`: no session has usable completed data and an active job or pending
   required session exists.
4. `failed`: no usable completed data exists, no work remains active, and
   required ingestion ended in failure.
5. `stale`: usable required data is otherwise complete, but season schedule
   coverage is missing or expired.
6. `completed`: at least one usable completed session exists, all currently
   archive-required sessions are satisfied, and schedule coverage is fresh.
7. `missing`: no other state applies, including a supported season with no
   stored data or only future/grace-period sessions.

Future or grace-period sessions are discovered but are not currently required
archive gaps. This allows a current season to be `completed` for all archive work
currently due without implying that the championship has finished.

## Endpoint 3: Read Backfill Job

### `GET /api/v1/backfill-jobs/{job_id}`

Returns `200 OK` for a known UUID and `404 Not Found` for an unknown job.
This endpoint is read-only and does not run parent aggregation.

The response contains:

- Job identity, season, request reason, status, and lifecycle timestamps.
- Fixed sanitized parent error when present.
- Explicit progress counts: total, pending, running, completed, failed, and
  terminal.
- Every child session in deterministic event/round/session order.
- Child attempt count, retry timestamp, lifecycle timestamps, and sanitized
  error.

No percentage is stored or returned in the first slice; clients can derive any
presentation from unambiguous counts.

Accepted shape:

```json
{
  "id": "3e18c9fd-a8eb-458f-b317-55867afdc53f",
  "season_year": 2024,
  "status": "running",
  "request_reason": "missing",
  "requested_at": "2026-07-28T12:00:00Z",
  "started_at": "2026-07-28T12:00:02Z",
  "heartbeat_at": "2026-07-28T12:00:32Z",
  "completed_at": null,
  "last_error": null,
  "progress": {
    "total": 72,
    "pending": 53,
    "running": 1,
    "completed": 18,
    "failed": 0,
    "terminal": 18
  },
  "sessions": [
    {
      "session_id": "210",
      "round_number": 1,
      "event_name": "Bahrain Grand Prix",
      "session_key": "race",
      "session_name": "Race",
      "status": "running",
      "attempt_count": 1,
      "queued_at": "2026-07-28T12:00:00Z",
      "started_at": "2026-07-28T12:00:02Z",
      "heartbeat_at": "2026-07-28T12:00:32Z",
      "next_retry_at": null,
      "completed_at": null,
      "last_error": null
    }
  ]
}
```

## Error Contract

Accepted error envelope:

```json
{
  "detail": {
    "code": "schedule_unavailable",
    "message": "Season schedule data is temporarily unavailable."
  }
}
```

Accepted mappings:

| Condition | Status | Stable code |
| --- | ---: | --- |
| Year below 2018 or after current UTC year | `422` | `season_year_out_of_range` |
| Malformed job UUID | `422` | FastAPI validation code |
| Unknown job UUID | `404` | `backfill_job_not_found` |
| Existing calendar natural key belongs to another source | `409` | `calendar_source_conflict` |
| Season changed repeatedly during planning | `409` | `season_planning_conflict` |
| Upstream schedule snapshot violates the pinned contract | `502` | `invalid_schedule_snapshot` |
| Schedule source is temporarily unavailable | `503` | `schedule_unavailable` |
| Database is unavailable | `503` | `database_unavailable` |
| Server cache/runtime configuration is invalid | `500` | `server_configuration_error` |

Raw exception text, tracebacks, cache paths, SQL parameters, URLs, headers,
cookies, and upstream bodies are never returned.

## Explicitly Excluded from the First Slice

- Manual cancellation or cancellation states.
- Manual forced refresh.
- Job listing/history endpoint.
- Results, entries, and lap endpoints.
- Telemetry endpoints.
- Pagination, sorting, or search over the season overview.
- Authentication and authorization.
- WebSocket or server-sent progress events.

The season overview is bounded to at most the supported championship calendar,
so pagination is unnecessary there. Lap and telemetry endpoints will require
separate pagination and query contracts.

## Implementation Sequence

1. Partially implemented: versioned API router, response schemas, supported-year
   validation, and the stable base error envelope. Database dependencies and
   internal-exception mappings remain with the endpoints that use them.
2. Implemented: pure/testable derived season-status policy.
3. Partially implemented: read-model queries for latest season membership,
   session eligibility/state, active-job summary, and derived season status.
   Job-progress reads remain planned.
4. Planned: connect the POST command to `ensure_season_backfill`.
5. Planned: add PostgreSQL API integration tests, including concurrent POSTs,
   preserved completed data, invalid years, and sanitized failures.
6. Planned: verify generated OpenAPI, Docker Compose startup, and real API-to-worker
   handoff without adding a live upstream request to automated tests.

No database migration is expected for this API slice.
