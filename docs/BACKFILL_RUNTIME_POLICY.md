# Backfill Runtime Policy

Status: **accepted; settings through freshness eligibility implemented**
Date: **2026-07-28**

## Purpose

This document defines the runtime policy that should surround the implemented
one-session FastF1 archive attempt before worker orchestration is built.

It covers:

- Retry budgets and failure classification.
- Backoff and jitter.
- Heartbeats and lease expiry.
- Crash recovery and stale-worker fencing.
- Current-season schedule and archive freshness.

It does not define job aggregation, cancellation, REST APIs, or UI behavior.
Typed settings, exception classification, deterministic backoff calculation,
transactional job-session claiming, and synchronized retry/terminal failure
transitions are implemented. Ownership-fenced heartbeat writes and claim-aware
atomic completion are also implemented. Bounded stale-lease recovery is
implemented. Deterministic coverage and archive-correction eligibility evaluation
is implemented. Worker heartbeat/recovery scheduling, job aggregation, and
freshness-triggered job creation remain unimplemented.

## Recommended Configuration

| Setting | Recommended value |
| --- | --- |
| Maximum attempts per job-session | 4 total: 1 initial attempt and up to 3 retries |
| Backoff base | 60 seconds |
| Backoff multiplier | 2 |
| Backoff cap | 15 minutes |
| Jitter | Equal jitter: random value from 50% to 100% of nominal delay |
| Heartbeat interval | 30 seconds |
| Lease timeout | 5 minutes |
| Recovery scan interval | 30 seconds |
| Initial worker concurrency | One FastF1 session at a time |
| Current-season coverage TTL | 6 hours |
| Historical-season coverage TTL | 30 days |
| Archive availability grace | 2 hours after scheduled session end |
| Archive correction checkpoints | 24 hours and 7 days after scheduled session end |

These values must be application configuration, not database schema constants.
All policy timestamps and eligibility comparisons use PostgreSQL UTC time.

The implemented `BackfillRuntimeSettings.from_environment()` loader reads:

- `BACKFILL_MAX_ATTEMPTS`
- `BACKFILL_BACKOFF_BASE_SECONDS`
- `BACKFILL_BACKOFF_MULTIPLIER`
- `BACKFILL_BACKOFF_CAP_SECONDS`
- `BACKFILL_JITTER_MIN_RATIO`
- `BACKFILL_HEARTBEAT_INTERVAL_SECONDS`
- `BACKFILL_LEASE_TIMEOUT_SECONDS`
- `BACKFILL_RECOVERY_SCAN_INTERVAL_SECONDS`
- `CURRENT_SEASON_COVERAGE_TTL_SECONDS`
- `HISTORICAL_SEASON_COVERAGE_TTL_SECONDS`
- `ARCHIVE_AVAILABILITY_GRACE_SECONDS`
- `ARCHIVE_CORRECTION_CHECKPOINTS_SECONDS`

The final setting is a comma-separated list of integer seconds. Invalid values
fail during settings construction instead of silently falling back.

## Attempt Counters

The two existing attempt counters have different responsibilities:

- `backfill_job_sessions.attempt_count` is the retry budget for that session
  inside one year-level job. It starts at zero for a new job and is limited to
  four attempts.
- `session_ingestions.attempt_count` is a monotonic lifetime attempt token for
  the database session. It must never be reset and will fence stale workers.

A manual or later freshness-triggered job receives a new job-session retry budget,
but the persistent session-ingestion token continues increasing.

## Failure Classification

### Retryable

Retry only failures that are expected to succeed without changing source data or
configuration:

- `fastf1_load_failed`
- `archive_target_changed`
- Transient database connection, serialization, deadlock, or availability errors
  classified from the original SQLAlchemy/psycopg exception

The sanitized code alone must not classify every database or persistence error as
retryable. The original in-process exception type and database error category make
that decision before only sanitized diagnostics are persisted.

### Terminal

Do not automatically retry:

- `fastf1_configuration_failed`
- `fastf1_normalization_failed`
- `archive_identity_mismatch`
- `archive_source_conflict`
- `archive_target_missing`
- Contract, integrity, and non-transient persistence errors
- Unknown or unexpected exceptions

Terminal classification prevents deterministic malformed data or configuration
errors from repeatedly contacting FastF1.

## Retry Transition

When a retryable attempt fails:

1. Keep the fixed sanitized error code and message.
2. If `backfill_job_sessions.attempt_count < 4`, change both the job-session and
   persistent session ingestion to `pending`.
3. Set both `next_retry_at` values to the same calculated UTC timestamp.
4. Clear both heartbeat fields.
5. Preserve any previous successful sporting snapshot, `completed_at`, and
   `source_updated_at`.

When the fourth attempt fails, or when a failure is terminal:

- Change both states to `failed`.
- Set `next_retry_at` to null.
- Preserve the last successful snapshot and completion metadata.

Successful replacement changes both states to `completed`, clears retry and error
fields, and commits the job-session and persistent session state atomically with
sporting data in the same worker-owned completion transaction.

## Backoff

For a failure after job attempt `n`, calculate:

```text
nominal_seconds = min(60 * 2^(n - 1), 900)
delay_seconds = random_between(0.5 * nominal_seconds, nominal_seconds)
next_retry_at = database_now_utc + delay_seconds
```

With four total attempts, the three automatic retry windows are:

- After attempt 1: 30–60 seconds.
- After attempt 2: 60–120 seconds.
- After attempt 3: 120–240 seconds.

Equal jitter avoids synchronized retries while retaining a meaningful minimum
delay. The cache-backed loader and one-session worker concurrency remain mandatory.

## Heartbeats

The worker writes a heartbeat immediately when it claims a job-session and then
every 30 seconds while loading, normalizing, or persisting.

Each heartbeat transaction must:

- Update `backfill_job_sessions.heartbeat_at`.
- Update `session_ingestions.heartbeat_at`.
- Update `backfill_jobs.heartbeat_at`.
- Require the job-session to remain `running` with the claimed job attempt.
- Require the session ingestion to remain `running` with the claimed monotonic
  session-attempt token.

If either conditional update affects zero rows, the worker has lost ownership and
must stop without persisting.

The implemented `heartbeat_archive_job_session` transaction performs one
ownership-checked refresh of all three heartbeat fields using PostgreSQL time.
The future worker must schedule this operation every 30 seconds and stop work if
ownership validation fails.

## Lease Expiry and Recovery

A running attempt is stale when its heartbeat is older than five minutes according
to PostgreSQL time. The five-minute lease allows ten consecutive 30-second
heartbeats to be missed before recovery.

Every 30 seconds, the recovery loop may claim stale rows in bounded batches with
`FOR UPDATE SKIP LOCKED`.

For each stale job-session:

1. Lock and re-read the job-session and persistent session-ingestion state.
2. Ignore rows that are no longer `running`.
3. Store fixed diagnostics:
   - Code: `worker_lease_expired`
   - Message: `The worker lease expired before session ingestion completed.`
4. If retry budget remains, move both rows to `pending` and calculate normal
   backoff from the attempt that lost its lease.
5. Otherwise move both rows to `failed`.
6. Clear heartbeat fields.
7. Never modify a completed session.

The implemented `recover_stale_archive_job_sessions` transaction selects a
bounded oldest-first batch with `FOR UPDATE SKIP LOCKED`. It requires both the
job-session and archive-owned persistent session to remain running with expired
heartbeat evidence, applies the fixed diagnostics above, preserves previous
completion/source timestamps, and uses the normal retry schedule. A fourth lost
attempt becomes terminal. Rows whose persistent session is no longer running are
left unchanged.

## Stale-Worker Fencing

Recovery alone is insufficient because an old worker can resume after its lease
has been recovered.

Every claim returns:

- The claimed `backfill_jobs.id`.
- The current `backfill_job_sessions.attempt_count`.
- The newly incremented `session_ingestions.attempt_count` as a monotonic fencing
  token.

Heartbeat, failure, and completion writes must condition on the job ID and both
ownership values.
Archive persistence must verify the expected session-attempt token and `running`
state inside its locked replacement transaction. A stale worker whose token no
longer matches must abort and cannot overwrite a newer attempt.

Heartbeat and failure transactions validate both tokens before committing.
Claim-aware archive persistence locks and validates the job-session, parent job,
target session, and persistent session ingestion before sporting writes. It marks
the job-session and persistent session completed in the same transaction as the
replacement snapshot. The direct non-job persistence path remains available for
controlled one-session operations.

The existing schema already contains the required counters and timestamps, so this
policy does not require a migration.

## Current-Season Freshness

### Season definition

The current season is the season whose `year` equals the current UTC calendar
year. Freshness comparisons use database time, not a worker's local clock.

### Coverage refresh

- Current-season schedule coverage is fresh for 6 hours.
- Historical-season schedule coverage is fresh for 30 days.
- A stale current season is served from existing completed data immediately while
  one active background coverage refresh is reused or created.
- A failed coverage refresh does not extend `coverage_valid_until`.
- The active-job unique index continues to prevent two simultaneous jobs for the
  same season.

### Session eligibility

A scheduled session becomes eligible for automatic FastF1 archive ingestion two
hours after `scheduled_end_at`.

- Future or grace-period sessions are not failures and are not claimed.
- A session with no usable scheduled end remains ineligible for automatic archive
  ingestion until schedule discovery repairs it or an explicit manual action is
  designed.
- If FastF1 remains unavailable after the grace period, the normal per-job retry
  policy applies.

### Archive correction checkpoints

Current or recently completed sessions are refreshed at most at these checkpoints:

1. After `scheduled_end_at + 24 hours`, when the latest successful
   `completed_at` is older than that checkpoint.
2. After `scheduled_end_at + 7 days`, when the latest successful
   `completed_at` is older than that checkpoint.

After a successful refresh at or beyond the seven-day checkpoint, the archive
snapshot is considered stable for automatic ingestion. Later corrections require a
manual backfill or a future explicitly accepted policy.

This checkpoint rule also applies when the calendar year changes before a recent
session reaches seven days.

### Serving behavior

- Existing completed sporting data remains queryable while coverage checks,
  refreshes, retries, or correction-checkpoint ingestion run.
- Selecting a current season never blocks on FastF1 when usable database data
  exists.
- A terminally failed current-season job may receive a fresh retry budget only
  through a later freshness-triggered or manual job; the active-job constraint and
  six-hour coverage TTL prevent tight recreation loops.

### Implemented eligibility boundary

The pure `evaluate_season_coverage` decision:

- Determines the current season from the database timestamp's UTC calendar year.
- Treats missing coverage as `missing`, a validity timestamp at or before database
  time as `stale`, and a later timestamp as `fresh`.
- Returns the configured TTL and the validity timestamp that a successful refresh
  performed at the supplied database time would receive.

The pure `evaluate_archive_ingestion` decision:

- Keeps a session with no scheduled end ineligible.
- Makes an incomplete archive eligible at the exact grace-period boundary.
- Treats a successful completion at a correction checkpoint as satisfying that
  checkpoint.
- Selects only the latest due unsatisfied checkpoint when a scan happens late, so
  missed checkpoints never cause immediate catch-up refreshes.
- Treats a successful completion at or beyond the final checkpoint as stable.

Both decisions require timezone-aware inputs and perform no database writes, job
creation, or upstream calls. Orchestration must supply PostgreSQL time and persist
or act on the returned decision separately.

## Implementation Sequence

1. Implemented: typed runtime settings and validation for the accepted values.
2. Implemented: retryable/terminal exception classification and deterministic
   backoff tests with injectable randomness and database time.
3. Implemented: job-session and persistent-session claim synchronization,
   retry/terminal failure transitions, and failure-write fencing.
4. Implemented: ownership-fenced heartbeat updates and claim-aware completion
   fencing inside archive persistence.
5. Implemented: bounded stale-lease recovery with normal backoff, terminal
   attempt exhaustion, completed-session preservation, and stale-worker fencing.
6. Implemented: current-season coverage and correction-checkpoint eligibility
   functions with UTC and exact-boundary validation.
7. Add parent-job aggregation for session outcomes.
8. Connect the placeholder worker only after the above behavior is covered by
   PostgreSQL integration tests.
