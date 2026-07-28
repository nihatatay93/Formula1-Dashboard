# Backfill Runtime Policy

Status: **accepted; worker execution implemented**
Date: **2026-07-28**

## Purpose

This document defines the implemented runtime policy around the one-session
FastF1 archive attempt and worker.

It covers:

- Retry budgets and failure classification.
- Backoff and jitter.
- Heartbeats and lease expiry.
- Crash recovery and stale-worker fencing.
- Current-season schedule and archive freshness.
- Cross-worker FastF1 request pacing and rate-limit cooldown.

It does not define REST APIs or UI behavior. Manual job cancellation is
explicitly deferred from the historical MVP; the operational shutdown behavior
is defined below.
Typed settings, exception classification, deterministic backoff calculation,
transactional job-session claiming, and synchronized retry/terminal failure
transitions are implemented. Ownership-fenced heartbeat writes and claim-aware
atomic completion are also implemented. Bounded stale-lease recovery is
implemented. Deterministic coverage and archive-correction eligibility evaluation
is implemented. Transactional parent-job aggregation is implemented. Worker
claiming, execution, heartbeat/recovery scheduling, failure/completion handling,
and aggregation are implemented. Cache-backed schedule discovery, atomic
calendar refresh, and freshness-triggered active-job creation/reuse are
implemented. A persistent PostgreSQL request gate now serializes FastF1 archive
session starts across workers, enforces a 90-second minimum interval, and applies
a one-hour global cooldown when FastF1 raises its rate-limit exception.

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
| Idle worker poll interval | 2 seconds |
| Initial worker concurrency | One FastF1 session at a time |
| Minimum interval between FastF1 archive session starts | 90 seconds |
| FastF1 rate-limit cooldown | 1 hour |
| Current-season coverage TTL | 6 hours |
| Historical-season coverage TTL | 30 days |
| Archive availability grace | 2 hours after scheduled session end |
| Archive correction checkpoints | 24 hours and 7 days after scheduled session end |

These values must be application configuration, not database schema constants.
All policy timestamps and eligibility comparisons use PostgreSQL UTC time.

The implemented `BackfillRuntimeSettings.from_environment()` loader reads:

- `BACKFILL_WORKER_POLL_INTERVAL_SECONDS`
- `FASTF1_ARCHIVE_SESSION_MIN_INTERVAL_SECONDS`
- `FASTF1_RATE_LIMIT_COOLDOWN_SECONDS`
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

FastF1's explicit rate-limit signal does not consume the job-session retry
budget. The lifetime session-ingestion token still increases because an owned
attempt was started and must remain monotonic for fencing.

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

### FastF1 rate limit

`fastf1_rate_limited` is handled separately from ordinary retryable load
failures:

1. Persist fixed, secret-free diagnostics.
2. Return both session states to `pending`.
3. Restore the job-session retry counter because the shared upstream budget,
   rather than session data, prevented the request.
4. Preserve the monotonic lifetime ingestion attempt token.
5. Set both session retry timestamps and the global `fastf1_archive` request
   gate to one hour after the database failure timestamp.

While that gate is closed, no worker can claim another FastF1 archive session.
This prevents a rate-limit response from consuming every pending session's retry
budget.

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
The worker schedules this operation every 30 seconds in a dedicated thread while
FastF1 loading, normalization, and persistence run synchronously. A
pre-persistence guard aborts before any sporting write when the heartbeat thread
has already failed. Claim-aware persistence remains the final ownership fence for
a failure racing the persistence transaction.

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

## Schedule Discovery and Job Planning

The implemented planner connects the pure decisions to database state:

1. Read coverage freshness using PostgreSQL time.
2. When coverage is missing or stale, load and fully normalize the FastF1 season
   index outside database locks.
3. Acquire a transaction-level advisory lock scoped to the season and recheck
   freshness.
4. Atomically upsert the latest championship calendar snapshot and coverage
   timestamps when refresh is still required.
5. Evaluate only sessions present in that latest successful snapshot.
6. Reuse the active pending/running job or create one new pending job.
7. Insert only missing job-session children that are archive eligible.

FastF1 3.8.3's public schedule frame omits session end timestamps. The schedule
loader uses the pinned cache-decorated F1 timing season index because it retains
real `StartDate`, `EndDate`, and `GmtOffset` values. Missing or invalid ends reject
the snapshot; no estimated duration is stored. Testing events and years before
2018 are excluded.

The season advisory lock serializes calendar persistence and job planning.
The existing partial unique active-job index remains a second database-level
guarantee. Concurrent callers can perform redundant cache-backed loading, but
after the lock the fresh committed database snapshot wins and both callers return
the same active job.

Rows absent from a later schedule are not deleted. Revision 3 discovery markers
identify current snapshot membership so removed rows remain queryable but are not
automatically queued. Loader, normalization, or source-conflict failure rolls back
the whole refresh and never extends coverage validity.

The complete contract is in `docs/SCHEDULE_DISCOVERY_DESIGN.md`.

## Parent-Job Aggregation

The implemented `aggregate_backfill_job` transaction locks every job-session row
in deterministic session order before locking its parent job. This matches the
child-before-parent lock order used by claim, failure, heartbeat, recovery, and
completion operations.

Parent status is monotonic:

- An empty or wholly unstarted job remains `pending`.
- Once work has started, the job remains `running` while any child is `pending`
  or `running`, including retry backoff periods.
- The job becomes `completed` only when every child is completed.
- The job becomes `failed` only when no child remains pending/running and at least
  one child failed. Completed children remain usable even when the job fails.
- A terminal parent is immutable; repeated aggregation preserves its terminal
  status and completion timestamp.

Terminal aggregation clears the parent heartbeat and assigns one PostgreSQL
completion timestamp. Failed child diagnostics are never copied to the parent;
the parent receives the fixed `session_ingestion_failed` code and message.
Aggregation returns all four child counts for future progress reporting. An empty
job never completes vacuously.

## Worker Execution

The implemented worker:

1. Validates runtime settings, cache configuration, and database connectivity
   before creating its readiness file.
2. Runs lease recovery and active-parent reconciliation immediately at startup
   and every 30 seconds afterward.
3. Polls every two seconds while idle and claims at most one eligible session.
   A claim first locks the persistent FastF1 request gate; successful claims
   reserve the next start at least 90 seconds later.
4. Processes the claimed session synchronously through the cache-backed loader,
   normalization, and claim-aware atomic persistence.
5. Runs periodic heartbeats in a separate thread for the duration of blocking
   session work.
6. Records retryable or terminal failure through the fenced transition, then
   aggregates the parent after success, failure, or ownership loss.
7. Reconciles every active parent during maintenance so a transient aggregation
   failure or worker restart cannot leave terminal child outcomes permanently
   hidden by an active parent.

The worker performs no upstream work when there is no pre-existing eligible
job-session. Schedule discovery and job creation remain a separate implemented
planner that a future REST season request will invoke.
Logs include operation and exception type but deliberately omit raw exception
text. SIGINT/SIGTERM stops new claims and idle waits; an in-process active attempt
is allowed to finish while its heartbeat thread continues. Local Compose grants
the worker a two-minute stop grace period. If the container is forcibly terminated
after that period, normal lease recovery handles the abandoned claim.

## Manual Cancellation Scope

Manual cancellation is not part of the historical MVP:

- Jobs and job-sessions retain only `pending`, `running`, `completed`, and
  `failed` states.
- No cancellation endpoint, cancellation state, or cancellation migration is
  introduced.
- An active synchronous FastF1 attempt is not interrupted because its cache,
  upstream request, and persistence boundaries do not provide a safe
  per-attempt cancellation point.
- Stopping the worker remains the operational escape hatch. Graceful shutdown
  allows an active attempt to finish; forced termination is repaired by normal
  lease recovery.

Job-level cancellation will be reconsidered after real ingestion durations are
measured or before production or multi-user operation requires it.

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
7. Implemented: transactional parent-job aggregation with monotonic status,
   deterministic locking, fixed diagnostics, terminal idempotency, and progress
   counts.
8. Implemented: single-concurrency worker execution with two-second polling,
   periodic heartbeats/recovery, fenced outcomes, parent reconciliation, and
   graceful shutdown.
9. Implemented: cache-backed FastF1 schedule discovery, atomic latest-snapshot
   persistence, and advisory-locked active-job creation/reuse.
10. Implemented: PostgreSQL-backed archive request pacing, distinct FastF1
    rate-limit classification, one-hour global cooldown, and retry-budget
    preservation.
