# Bounded Historical Telemetry

## Scope

Historical telemetry is ingested only for an explicitly requested stored lap.
It is never loaded for a season, included in a season overview, or embedded in
session/result/lap-summary responses. Standard PostgreSQL is the implemented
storage engine; the evidence and TimescaleDB review triggers remain in
`docs/TELEMETRY_STORAGE_DECISION.md`.

## Persistent Model

Alembic Revision 6 adds:

- `lap_telemetry_ingestions`: exactly one command/worker lifecycle row per lap,
  with pending/running/completed/failed status, attempt count, timestamps,
  heartbeat, retry eligibility, fixed safe failure diagnostics, sample count,
  and the completed sporting snapshot timestamp used by the request.
- `lap_telemetry_samples`: normalized, deterministic sample-index rows
  containing lap/session time, distance, speed, RPM, gear, throttle, brake,
  DRS, optional X/Y/Z, source, and record state.

Samples are unique by `(lap_id, sample_index)` and read through a matching
index. Range checks reject invalid channel values. Both telemetry tables
cascade only with their owning `laps` row; unrelated sporting relationships
retain their restrictive deletion policy.

## Command and Worker

`POST /api/v1/sessions/{session_id}/entries/{session_entry_id}/laps/{lap_number}/telemetry`
validates that the exact stored lap belongs to a completed historical snapshot.
The command creates at most one persistent state row:

- `202 queued`: new, failed, or stale telemetry was reset to pending.
- `202 reused`: compatible pending/running work already exists.
- `200 available`: a compatible completed sample set already exists.

The single-concurrency worker always looks for archive session work first. Only
when no archive session is claimable does it claim the oldest retry-eligible
telemetry lap with `FOR UPDATE SKIP LOCKED`. The claim captures an attempt token
and sporting snapshot timestamp, derives the exact season/round/session,
driver-number-or-abbreviation, and lap request, then loads through the
persistent serialized FastF1 cache and shared request ledger.

Normalization is pure. Completion revalidates claim ownership and the current
sporting snapshot, deletes the previous sample set, inserts the normalized set,
and marks the state completed in one transaction. Blocking FastF1 work is
heartbeated. Retryable upstream and request-budget failures use the existing
four-attempt equal-jitter policy. Expired claims use bounded lease recovery.
Failure fields and worker logs never retain raw upstream error details.

## Read Contract

`GET /api/v1/sessions/{session_id}/entries/{session_entry_id}/laps/{lap_number}/telemetry`
returns:

- ingestion lifecycle and fixed failure details;
- current and telemetry-source sporting snapshot timestamps plus compatibility;
- `data_available`, sample count, and sample-index keyset page;
- at most 500 samples by default and 1,000 at the hard maximum.

Pending and failed requests return `200` with state and an empty sample page so
clients can poll one contract. A valid lap without a command returns stable
`409 telemetry_not_requested`. Missing targets return `404 lap_not_found`;
incomplete sporting snapshots return `409 session_data_unavailable`. Both
operations use `Cache-Control: no-store`.

## Verification

Revision 6 was verified by upgrade, downgrade to Revision 5, re-upgrade, and
`alembic check` against isolated PostgreSQL 17. Focused tests cover pure
normalization, database metadata, idempotent commands, snapshot-bound atomic
replacement, attempt fencing, lease recovery, bounded endpoint behavior, and
OpenAPI. The full backend, frontend component, desktop/mobile browser,
production build, and Compose checks also pass.
