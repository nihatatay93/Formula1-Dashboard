# FastF1 One-Session Ingestion Contract

Status: **accepted and implemented for one-session execution**
Date: **2026-07-28**

## Purpose

This contract defines how one complete FastF1 session snapshot is validated and
replaces the previously stored FastF1 archive snapshot for the same database
session.

An upsert updates or creates rows that are present in a new snapshot. It does not
remove previously stored rows that are absent from that snapshot. Without explicit
replacement behavior, corrected upstream data can leave obsolete entries, results,
or laps in the database.

## Scope

The contract applies to historical `fastf1_archive` sporting data:

- Global driver identity.
- Session entries.
- Session results.
- Lap summaries.
- The session ingestion completion state.

It does not cover:

- Schedule discovery.
- Worker claiming, retry, heartbeat, or lease policies.
- Telemetry or weather.
- Live SignalR data.
- Reconciliation between provisional live data and finalized archive data.

The persistence implementation refuses to replace a session containing non-archive
sporting rows or ingestion state. It does not delete or overwrite `live_signalr`
provisional data; live finalization requires a separate reconciliation contract.

## Deterministic Entry Identity

Entry identity is scoped to one session:

1. When a non-empty FastF1/Jolpica driver ID exists, use
   `driver:jolpica:<normalized-driver-id>`.
2. Otherwise, when a valid racing number exists, use
   `car-number:<normalized-racing-number>`.
3. If neither value exists, fail normalization for the session.

Driver IDs are trimmed and case-normalized. Racing numbers are validated as positive
integers and stored in canonical decimal form. For example, `01` becomes `1`.

The racing-number fallback never creates or merges a global driver. Its
`session_entries.driver_id` remains null until a verified external identity becomes
available. Names and abbreviations are display and lap-association data only; they
are never global identity keys.

## Pre-Transaction Load and Validation

FastF1 loading, normalization, and validation occur before the replacement
transaction begins. This keeps network and cache work outside database locks and
ensures that malformed data cannot partially replace a stored session.

The normalized snapshot must satisfy:

- At least one session entry exists.
- Entry keys are unique within the session.
- Non-null driver IDs, racing numbers, and abbreviations are unique within the
  supported 2018+ session.
- Every entry has a usable display name.
- Every lap maps to exactly one normalized entry.
- Lap numbers are positive integers and unique per entry.
- Integer-like FastF1 values are integral before conversion.
- Durations are non-negative and converted to exact integer microseconds.
- Required booleans remain booleans; nullable booleans preserve unknown as null.
- Race and sprint snapshots contain exactly one first-place result.
- Source result time is split into winner elapsed time and following-driver leader
  gap time only for race-like sessions.

Any validation failure rejects the complete candidate snapshot.

## Archive Session Loading

The implemented loader accepts one deterministic request containing:

- A season year of 2018 or later.
- A positive championship round number.
- A non-empty FastF1 session identifier.

Round numbers are used instead of fuzzy event-name matching. The loader creates and
activates an absolute persistent cache directory, retains FastF1's cache-version
validation, enables the raw HTTP requests cache, and does not force cache renewal.

FastF1 loading is explicitly configured with:

- `laps=True`
- `telemetry=False`
- `weather=False`
- `messages=True`

Loading race-control messages allows FastF1 to populate deleted-lap state and
reason. FastF1 uses process-global cache state, so cache activation and session
loading are serialized with one process-local lock. Cross-process job concurrency
remains worker-orchestration work.

The loader returns only the loaded session name, results table, laps table, and
original request. It does not normalize or persist data itself.

## Database Target Binding

The one-session vertical slice accepts a database `session_id`. Before contacting
FastF1, it reads the target session together with its event and derives the loader
request from the stored season year, championship round number, and upstream
session name. Callers therefore do not independently supply an archive identity
that could disagree with the persistence target.

After loading, the returned request must equal the derived request and the loaded
FastF1 session name must match the stored session name after whitespace and
case normalization. An identity mismatch fails before normalization and before the
replacement transaction. The expected identity is checked again after the target
session and event rows are locked, so a concurrent metadata change aborts the
replacement.

## Atomic Replacement

After validation succeeds, persistence uses one database transaction:

1. Lock the target `sessions` and `events` rows with `SELECT ... FOR UPDATE`.
2. Verify that the locked season, round, and session name still match the request.
3. Verify that the session does not contain sporting rows or ingestion state owned
   by another source.
4. Upsert global drivers by verified Jolpica driver ID.
5. Temporarily clear archive entry driver links and racing numbers inside the
   transaction so corrected number assignments and fallback-to-verified key
   transitions cannot violate partial unique indexes.
6. Upsert session entries by `(session_id, entry_key)`.
7. Upsert results by `session_entry_id`.
8. Upsert laps by `(session_entry_id, lap_number)` in bounded batches.
9. Delete archive-owned results and laps absent from the new snapshot.
10. Delete archive-owned session entries absent from the new snapshot, after their
   children have been removed.
11. Mark the session ingestion `completed` and `finalized`.
12. Commit once.

Global driver rows are not session-owned and must never be deleted by session
replacement.

PostgreSQL readers therefore observe either the previous committed snapshot or the
new committed snapshot. They never observe a deliberately emptied or partially
replaced session.

## Failure Behavior

FastF1 loading or normalization failure does not open the replacement transaction.

If persistence fails, the entire replacement transaction rolls back and the
previous committed sporting snapshot remains available. Recording a sanitized
worker failure in a separate transaction and choosing retry timing remain later
orchestration work.

The implementation must never delete the old snapshot before the new snapshot has
been fully loaded and validated.

## Idempotency

Reprocessing the same normalized FastF1 snapshot produces the same logical database
state:

- Existing natural-key rows are updated.
- Missing natural-key rows are inserted.
- Archive-owned stale rows are removed.
- No duplicate driver, entry, result, or lap rows are created.

The snapshot is authoritative only for the single database session being processed.
No replacement operation may affect another session, event, or season.

## Current Implementation Boundary

Implemented:

- Pure FastF1 results-and-laps normalization.
- Deterministic entry-key construction.
- Null, scalar, integer, duration, decimal, boolean, and speed validation.
- Race/sprint result-time normalization.
- Lap-to-entry association and natural-key duplicate detection.
- Deterministic 2018+ year/round/session archive requests.
- Persistent FastF1 cache directory creation and activation.
- Process-local serialization of FastF1 cache activation and session loading.
- Explicit laps/messages loading with telemetry and weather disabled.
- Database-derived season, round, and session-name loader requests.
- Loaded-request and loaded-session-name verification before persistence.
- Locked target-identity revalidation inside the replacement transaction.
- Composition of loading, normalization, and persistence into one callable
  database-session vertical slice.
- Transaction ownership and target-session row locking.
- Non-archive entry, result, lap, and ingestion-state protection.
- Driver, entry, result, and bounded-batch lap upserts.
- Stale archive row deletion without deleting global drivers.
- Atomic `completed` and `finalized` ingestion-state updates.
- Rollback preservation and idempotent stable natural-key rows.

Not implemented:

- Pending/running/failed ingestion state transitions.
- Failure recording after persistence rollback.
- Worker execution and retry behavior.
