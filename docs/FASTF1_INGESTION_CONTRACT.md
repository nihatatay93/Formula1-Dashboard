# FastF1 One-Session Ingestion Contract

Status: **accepted; normalization implemented, persistence pending**
Date: **2026-07-28**

## Purpose

This contract defines how one complete FastF1 session snapshot is validated and
eventually replaces the previously stored FastF1 archive snapshot for the same
database session.

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

The first persistence implementation must refuse to replace a session containing
non-archive sporting rows. It must not delete or overwrite `live_signalr` provisional
data until the live finalization contract is designed.

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

## Atomic Replacement

After validation succeeds, persistence will use one database transaction:

1. Lock or otherwise verify exclusive write ownership for the database session.
2. Verify that the session does not contain sporting rows owned by another source.
3. Upsert global drivers by verified Jolpica driver ID.
4. Upsert session entries by `(session_id, entry_key)`.
5. Upsert results by `session_entry_id`.
6. Upsert laps by `(session_entry_id, lap_number)`.
7. Delete archive-owned results and laps absent from the new snapshot.
8. Delete archive-owned session entries absent from the new snapshot, after their
   children have been removed.
9. Mark the session ingestion `completed` and `finalized`.
10. Commit once.

Global driver rows are not session-owned and must never be deleted by session
replacement.

PostgreSQL readers therefore observe either the previous committed snapshot or the
new committed snapshot. They never observe a deliberately emptied or partially
replaced session.

## Failure Behavior

FastF1 loading or normalization failure does not open the replacement transaction.

If persistence fails, the entire replacement transaction rolls back and the
previous committed sporting snapshot remains available. The worker will record the
sanitized failure state in a separate transaction after rollback; retry timing is a
later orchestration decision.

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

Not implemented:

- FastF1 session loading and cache activation.
- Database upserts and stale-row deletion.
- Exclusive session writer enforcement.
- Session ingestion state transitions.
- Worker execution and retry behavior.
