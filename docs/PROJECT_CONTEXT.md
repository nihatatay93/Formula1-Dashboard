# Formula1 Dashboard — Project Context

## Project Purpose

Formula1 Dashboard is a Formula 1 data platform that will expose live timing and historical race data through one backend API and one shared database.

The system is intended to:

- Receive live Formula 1 timing data through SignalR.
- Backfill historical season, event, and session data through FastF1.
- Store live and historical data in a shared data model.
- Provide a web dashboard for season and session data.
- Support a future SwiftUI iOS application through the same backend API.
- Prevent web and iOS clients from connecting directly to upstream Formula 1 data sources.
- Keep F1TV/FastF1 tokens, credentials, cookies, and similar sensitive values out of all clients.

## Current Architecture

The local-development scaffold, four database migrations, locked FastF1 runtime, schedule discovery and season job planner, a managed database-bound one-session FastF1 archive worker, the first historical API slice, and the first product dashboard milestone are implemented. The API slice provides `POST /api/v1/seasons/{season_year}/backfill`, `GET /api/v1/seasons/{season_year}`, and `GET /api/v1/backfill-jobs/{job_id}` with strict response/error contracts, 2018-through-current-UTC-year validation, JavaScript-safe decimal-string database identifiers, UTC timestamp normalization, dynamic `200/202` command behavior, and client-safe failures. The season overview reads one repeatable PostgreSQL snapshot and never writes or contacts FastF1. The job-progress endpoint reads one repeatable snapshot, derives internally consistent counts, preserves deterministic round/session ordering, and never runs parent aggregation. The idempotent backfill command synchronously checks schedule coverage through the persistent FastF1 cache, delegates all planning and concurrency control to the season planner, creates or reuses one active job, and exposes its polling location without performing session ingestion in the API process. The managed ingestion flow adds observable pending/running/completed/failed session-ingestion state and fixed sanitized failure diagnostics around serialized cache-backed loading, pure sporting-data normalization, and atomic archive persistence. Validated runtime settings, retryable/terminal exception classification, deterministic equal-jitter backoff calculations, transactional job-session claiming, synchronized retry/terminal failure transitions, ownership-fenced heartbeat writes, claim-aware atomic completion, bounded stale-lease recovery, deterministic season/session freshness eligibility, transactional parent-job aggregation, and single-concurrency worker execution are implemented. Claims use a persistent PostgreSQL FastF1 request gate plus row locking and return job-attempt and monotonic session-attempt ownership tokens; heartbeat, failure, and completion writes validate both tokens. Archive session starts are paced at least 90 seconds apart across workers. FastF1's explicit rate-limit exception closes the global gate for one hour without consuming the job-session retry budget. Recovery fences the lost claim by leaving running state before a retry can be claimed. Freshness functions evaluate UTC coverage expiry, archive grace, and correction checkpoints. The season planner uses FastF1's curated schedule as the championship membership and round-number authority, retains exact private-index boundaries for matched events, hydrates missing events from exact per-session timing metadata, persists the latest calendar snapshot atomically, and creates or reuses one active year job under a season advisory lock. Aggregation locks all child rows before the parent, preserves monotonic job state, and returns progress counts. The worker polls eligible jobs, maintains heartbeats during blocking FastF1 work, runs recovery/parent reconciliation every 30 seconds, applies fenced outcomes, and stops gracefully without taking new work. The React dashboard selects supported seasons, presents coverage and session-state visualizations, starts or reuses backfill jobs, and polls active job progress through the backend only. The database contains the backfill control plane, request coordination, schedule membership markers, and normalized sporting-data tables. Historical result/lap views, telemetry, and live timing ingestion are not yet implemented.

Implemented services in `compose.yaml`:

- `db`: PostgreSQL with a persistent `postgres_data` volume and a health check.
- `migrate`: One-shot Alembic service that upgrades the database before the API and worker start.
- `api`: FastAPI application with liveness and database-readiness endpoints.
- `worker`: Single-concurrency archive process built from the backend image. It validates configuration/database readiness, processes existing eligible job-sessions, heartbeats active claims, and performs periodic recovery/aggregation maintenance.
- `frontend`: React, TypeScript, and Vite application for season selection, coverage and event/session state visualization, backfill commands, and active-job polling.

Implemented supporting infrastructure:

- A persistent `fastf1_cache` Docker volume is declared for the worker.
- The API and worker mount the same persistent `fastf1_cache` volume; the API uses it only for synchronous schedule discovery and the worker uses it for archive session loading.
- FastF1 cache activation and session loads are serialized within one process.
- FastF1 archive session starts are coordinated across worker processes through
  a PostgreSQL gate with 90-second pacing and a one-hour explicit rate-limit
  cooldown.
- One-session archive requests are derived from stored session and event identity, checked against the loaded FastF1 session, and revalidated under database row locks before replacement.
- Archive attempts expose committed running state, reject overlap and non-archive ownership, and record fixed secret-free failure diagnostics without deleting a previous completed snapshot.
- Claimed archive attempts can refresh all three heartbeat fields with one PostgreSQL timestamp and complete both session states atomically with sporting-data replacement.
- Expired synchronized leases can be recovered in bounded `SKIP LOCKED` batches without deleting or replacing a previous successful archive snapshot.
- Pure policy decisions identify missing, fresh, or stale season coverage and initial, checkpoint, pending, or stable archive eligibility from PostgreSQL timestamps.
- FastF1 schedule discovery uses the same serialized persistent cache boundary and requires real session start/end timestamps from the pinned F1 timing season index.
- Successful calendar refreshes atomically mark latest-snapshot membership without deleting rows absent from a later schedule.
- Season planning uses a PostgreSQL advisory lock to refresh stale coverage, reuse one active job, and append only missing eligible job-session rows.
- Parent jobs can be transactionally aggregated from locked session outcomes into monotonic pending, running, completed, or failed state with progress counts.
- The worker claims and processes one FastF1 session at a time, heartbeats in a dedicated thread, reconciles abandoned leases/active parents every 30 seconds, and polls every two seconds while idle.
- Host-side Python editing uses a native macOS Python 3.13 environment synchronized from `backend/uv.lock`; Docker-created virtual environments are not reused by the host editor.
- The backend uses Python 3.13, `uv`, FastAPI, FastF1 3.8.3, pandas, SQLAlchemy 2, Alembic, psycopg, Uvicorn, pytest, and Ruff.
- The frontend uses Node.js 24, npm, React, TypeScript, and Vite.
- PostgreSQL trust authentication is restricted to loopback-bound local development and must not be reused for production.
- SQLAlchemy uses synchronous sessions with the explicit `postgresql+psycopg` dialect.
- The application never calls `create_all`; Alembic is the only schema-authoring mechanism.

Target data flow:

1. The web dashboard or future iOS application communicates only with the backend API.
2. The backend will use FastF1 for historical data and SignalR for live timing.
3. Ingestion processes will normalize data into PostgreSQL.
4. REST endpoints will serve historical data, while WebSocket connections will serve live timing.
5. Live session data will initially be stored as provisional data and finalized against FastF1 archive data after the session.

## Directory Structure

Current repository structure:

```text
Formula1-Dashboard/
├── AGENTS.md
├── README.md
├── compose.yaml
├── backend/
│   ├── alembic/
│   │   ├── versions/
│   │   ├── env.py
│   │   └── script.py.mako
│   ├── app/
│   │   ├── api/
│   │   │   ├── backfill_job.py
│   │   │   ├── backfill_jobs.py
│   │   │   ├── contracts.py
│   │   │   ├── dependencies.py
│   │   │   ├── errors.py
│   │   │   ├── router.py
│   │   │   ├── season_overview.py
│   │   │   ├── season_status.py
│   │   │   └── seasons.py
│   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── engine.py
│   │   │   ├── naming.py
│   │   │   └── session.py
│   │   ├── ingestion/
│   │   │   ├── archive_attempt.py
│   │   │   ├── archive_ingestion.py
│   │   │   ├── archive_persistence.py
│   │   │   ├── backfill_worker.py
│   │   │   ├── backfill_orchestration.py
│   │   │   ├── fastf1_loader.py
│   │   │   ├── fastf1_normalization.py
│   │   │   ├── fastf1_schedule.py
│   │   │   ├── freshness_policy.py
│   │   │   ├── runtime_policy.py
│   │   │   └── season_backfill.py
│   │   ├── main.py
│   │   └── worker.py
│   ├── tests/
│   │   ├── test_api_contracts.py
│   │   ├── test_api_foundation.py
│   │   ├── test_archive_attempt.py
│   │   ├── test_archive_ingestion.py
│   │   ├── test_archive_persistence.py
│   │   ├── test_backfill_job.py
│   │   ├── test_backfill_job_endpoint.py
│   │   ├── test_backfill_orchestration.py
│   │   ├── test_database_integration.py
│   │   ├── test_database_metadata.py
│   │   ├── test_fastf1_loader.py
│   │   ├── test_fastf1_normalization.py
│   │   ├── test_fastf1_schedule.py
│   │   ├── test_freshness_policy.py
│   │   ├── test_health.py
│   │   ├── test_runtime_policy.py
│   │   ├── test_season_endpoint.py
│   │   ├── test_season_backfill.py
│   │   ├── test_season_backfill_endpoint.py
│   │   ├── test_season_backfill_endpoint_integration.py
│   │   ├── test_season_overview.py
│   │   ├── test_season_status.py
│   │   ├── test_sporting_data_integration.py
│   │   └── test_worker.py
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── uv.lock
├── docs/
│   ├── BACKFILL_RUNTIME_POLICY.md
│   ├── DATABASE_DESIGN.md
│   ├── FASTF1_INGESTION_CONTRACT.md
│   ├── HISTORICAL_API_DESIGN.md
│   ├── PROJECT_CONTEXT.md
│   ├── SCHEDULE_DISCOVERY_DESIGN.md
│   └── SPORTING_DATA_DESIGN.md
└── frontend/
    ├── src/
    ├── Dockerfile
    ├── package.json
    ├── package-lock.json
    └── vite.config.ts
```

- `backend/app/`: FastAPI and worker process source.
- `backend/app/api/`: Versioned historical API, strict response/error models, supported-year validation, season and job read models/routes, and the idempotent backfill command boundary.
- `backend/app/db/`: SQLAlchemy metadata, connection configuration, session factory, and Revision 1–4 models.
- `backend/app/ingestion/`: Managed attempt state, schedule discovery and season planning, transactional backfill claiming/failure/aggregation transitions, single-concurrency worker execution, database-bound one-session orchestration, cache-backed loading, pure upstream-to-domain normalization, atomic archive persistence, and runtime/freshness policy primitives.
- `backend/alembic/`: Alembic environment and reviewed migration revisions.
- `backend/tests/`: Backend tests.
- `frontend/src/`: React dashboard source.
- `docs/`: Architecture, decisions, and persistent project context.
- `docs/BACKFILL_RUNTIME_POLICY.md`: Accepted retry, backoff, heartbeat, lease recovery, fencing, current-season freshness, parent aggregation, and worker execution policy.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic conventions, migration phases, tables, constraints, indexes, and recovery behavior.
- `docs/FASTF1_INGESTION_CONTRACT.md`: Accepted one-session validation, identity, atomic replacement, failure, and idempotency contract.
- `docs/HISTORICAL_API_DESIGN.md`: Accepted and implemented first historical season and backfill REST API contract.
- `docs/SCHEDULE_DISCOVERY_DESIGN.md`: Implemented FastF1 schedule source, atomic calendar snapshot, membership, and active-job planning contract.
- `docs/SPORTING_DATA_DESIGN.md`: Evidence-based implemented Revision 2 driver, entry, result, and lap schema.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `AGENTS.md`: Mandatory repository workflow and context rules.

## Technical Decisions

### Repository language

- Decision: Source documentation and project communication will continue in English.
- Rationale: The user explicitly selected English for the project.
- Date: 2026-07-27
- Status: accepted

### Single project workspace

- Decision: All source code, documentation, and Docker files must remain under `/Users/nihatatay/Desktop/Projects/Formula1-Dashboard`.
- Rationale: Isolate this project from other Formula 1 work and maintain one authoritative workspace.
- Date: 2026-07-27
- Status: accepted

### Local-first Docker development

- Decision: Build the local Docker development environment before any production deployment work.
- Rationale: Develop and validate the services in a controlled, reproducible environment.
- Date: 2026-07-27
- Status: implemented

### First development phase

- Decision: Historical backfill infrastructure is the first development phase.
- Rationale: Establish the shared data model, ingestion state, and API behavior before adding live ingestion.
- Date: 2026-07-27
- Status: accepted

### Backend foundation

- Decision: Use Python and FastAPI for the backend and ingestion processes.
- Rationale: Integrate naturally with FastF1 and provide one API for web and iOS clients.
- Date: 2026-07-27
- Status: implemented

### Host Python editor environment

- Decision: Use Homebrew Python 3.13 and `uv` to create an ignored native `backend/.venv` for VS Code, while continuing to use the same committed `pyproject.toml` and `uv.lock` as Docker.
- Rationale: A virtual environment created inside the Linux backend container contains a Linux interpreter path and binaries that cannot be used by macOS VS Code.
- Date: 2026-07-28
- Status: implemented

### Frontend foundation

- Decision: Use React and TypeScript with Vite for the web dashboard.
- Rationale: Provide a typed, component-based dashboard with a lightweight development server.
- Date: 2026-07-27
- Status: implemented

### First dashboard product slice

- Decision: Build the first dashboard directly over the implemented `/api/v1` season and backfill contracts, default to the current UTC season, support 2018 onward, poll active jobs every two seconds, and keep telemetry outside season-level responses.
- Rationale: Provide an immediately useful visualization without inventing client-only data, bypassing the backend, or expanding the API and dependency surface before historical result views are designed.
- Date: 2026-07-28
- Status: implemented

### Database foundation

- Decision: Use PostgreSQL as the primary database and evaluate TimescaleDB separately for high-frequency telemetry.
- Rationale: Store relational race data reliably while keeping a telemetry-specific optimization path open.
- Date: 2026-07-27
- Status: accepted

### Migration approach

- Decision: Use Alembic for schema migrations.
- Rationale: Maintain explicit, reviewable, reversible schema history.
- Date: 2026-07-27
- Status: accepted

### Phased database design

- Decision: Separate the control plane, sporting data, and telemetry into independent migration phases.
- Rationale: Validate job orchestration and session ingestion before committing to a high-volume telemetry schema or TimescaleDB.
- Date: 2026-07-27
- Status: implemented

### SQLAlchemy database layer

- Decision: Use SQLAlchemy 2-style declarative models with synchronous sessions and psycopg for the first backfill phase.
- Rationale: FastF1 processing is blocking, and one synchronous database model avoids unnecessary dual sync/async infrastructure at the start.
- Date: 2026-07-27
- Status: implemented

### Migration service

- Decision: Run Alembic through a one-shot `migrate` Compose service and make the API and worker wait for its successful completion.
- Rationale: Prevent multiple long-running services from racing to apply schema changes.
- Date: 2026-07-27
- Status: implemented

### Derived season status

- Decision: Derive season status from discovered coverage, active jobs, and session ingestion state rather than storing a mutable season status column.
- Rationale: Prevent season state from drifting away from its underlying job and session records.
- Date: 2026-07-27
- Status: accepted

### Extensible state values

- Decision: Store ingestion statuses, data sources, record states, and request reasons as text protected by named check constraints rather than native PostgreSQL enums.
- Rationale: Keep value changes and Alembic upgrades/downgrades straightforward while preserving database validation.
- Date: 2026-07-27
- Status: implemented

### No Redis at the start

- Decision: Do not add Redis or another external background-job system until a demonstrated need exists.
- Rationale: Avoid unnecessary operational complexity in the first phase.
- Date: 2026-07-27
- Status: implemented

### Client isolation from upstream sources

- Decision: Web and iOS clients use only the project backend API and never connect directly to Formula 1 data endpoints.
- Rationale: Centralize normalization and prevent credentials from reaching clients.
- Date: 2026-07-27
- Status: accepted

### Historical ingestion boundary

- Decision: Begin historical ingestion with the 2018 season; do not fetch the complete Formula 1 history.
- Rationale: FastF1 detailed timing support begins in 2018, and the user does not require earlier results-only coverage.
- Date: 2026-07-28
- Status: accepted

### Driver and racing-number identity

- Decision: Identify drivers with an internal database ID and a stable external driver identifier. Store racing number as a session-entry attribute and never use it as a global driver identifier.
- Rationale: Racing number `1` follows the reigning world champion and can belong to different drivers in different seasons. Other racing numbers can also change owners over time.
- Date: 2026-07-28
- Status: implemented

### Racing-number uniqueness scope

- Decision: For supported 2018+ data, enforce uniqueness of a non-null racing number only within one session.
- Rationale: Prevent duplicate car entries inside a modern session while allowing the same number to belong to different drivers across sessions and seasons.
- Date: 2026-07-28
- Status: implemented

### Unresolved driver entries

- Decision: Allow `session_entries.driver_id` to be null and use a deterministic session-local `entry_key` when a verified global driver identity is unavailable.
- Rationale: Preserve provisional or incomplete entries without incorrectly merging drivers by name, abbreviation, or racing number.
- Date: 2026-07-28
- Status: implemented

### Result time normalization

- Decision: Store `elapsed_time_us`, `gap_to_leader_us`, and `gap_to_leader_laps` rather than one source-shaped result time.
- Rationale: FastF1 uses total elapsed time for the winner and leader gaps for following finishers, so one undifferentiated time field would be ambiguous.
- Date: 2026-07-28
- Status: implemented

### Lap summary breadth

- Decision: Store lap-level speed-trap and data-quality fields in addition to timing, pit, tyre, and position summaries.
- Rationale: These values are useful low-volume lap summaries and are not high-frequency telemetry.
- Date: 2026-07-28
- Status: implemented

### Deleted-lap accuracy

- Decision: Load race-control messages for 2018+ sporting backfills while leaving telemetry and weather disabled.
- Rationale: FastF1 requires race-control messages to populate deleted-lap state and reason reliably.
- Date: 2026-07-28
- Status: implemented

### FastF1 runtime version

- Decision: Pin FastF1 to version 3.8.3 and keep pandas as an explicit compatible direct dependency managed by `uv.lock`.
- Rationale: The sporting-data schema and normalization rules were inspected against FastF1 3.8.3, so an exact FastF1 version prevents an unnoticed upstream data-contract change.
- Date: 2026-07-28
- Status: implemented

### One-session archive replacement

- Decision: Treat a fully normalized FastF1 session as the authoritative archive snapshot for one database session and replace its archive-owned sporting rows atomically.
- Rationale: Upserts alone cannot remove rows that disappear from a corrected upstream snapshot. Atomic replacement removes stale archive rows without exposing an empty or partially replaced session.
- Date: 2026-07-28
- Status: implemented

### Replacement safety boundary

- Decision: Fully load and validate FastF1 data before opening the replacement transaction; roll back all sporting writes on failure, never delete global drivers, and refuse to replace sessions containing non-archive sporting rows.
- Rationale: Preserve the previous complete snapshot on failure and prevent the historical ingestion phase from damaging future provisional live data.
- Date: 2026-07-28
- Status: implemented

### Archive entry-key algorithm

- Decision: Use `driver:jolpica:<normalized-driver-id>` when a verified driver ID exists, otherwise use session-local `car-number:<normalized-racing-number>`, and fail when neither exists.
- Rationale: Produce deterministic idempotency keys without globally identifying a driver by number, name, or abbreviation.
- Date: 2026-07-28
- Status: implemented

### Pure normalization boundary

- Decision: Transform FastF1 results and laps into immutable normalized records before persistence.
- Rationale: Make upstream type handling and validation deterministic and testable without database or network side effects.
- Date: 2026-07-28
- Status: implemented

### Deterministic FastF1 loader requests

- Decision: Load archive sessions by 2018+ season year, championship round number, and non-empty session identifier rather than fuzzy event-name matching.
- Rationale: Round-based requests align with stored event identity and avoid ambiguous event-name resolution.
- Date: 2026-07-28
- Status: implemented

### FastF1 loader data scope

- Decision: Load laps and race-control messages while explicitly disabling telemetry and weather.
- Rationale: Produce the accepted sporting snapshot and reliable deleted-lap fields without downloading high-volume telemetry or unused weather data.
- Date: 2026-07-28
- Status: implemented

### FastF1 cache and concurrency

- Decision: Require an absolute persistent cache path, keep FastF1 cache-version validation and HTTP request caching enabled, avoid forced renewal, and serialize cache activation and session loading with one process-local lock.
- Rationale: Reuse upstream responses safely and prevent aggressive parallel FastF1 access or races around FastF1's process-global cache configuration.
- Date: 2026-07-28
- Status: implemented

### Cross-worker FastF1 request pacing

- Decision: Coordinate FastF1 archive session starts through a persistent PostgreSQL gate, require at least 90 seconds between starts, and apply a one-hour global cooldown when FastF1 raises its explicit rate-limit exception. A rate-limit response does not consume the job-session retry budget, while the lifetime ingestion attempt token remains monotonic.
- Rationale: The first real 2018 backfill exhausted FastF1's hourly request budget and then rapidly consumed retry budgets across pending sessions. Database-backed coordination survives restarts and applies consistently if more than one worker process exists.
- Date: 2026-07-28
- Status: implemented

### Historical personal-best nullability

- Decision: Preserve a missing historical FastF1 `IsPersonalBest` value as null instead of rejecting the entire session or inventing `false`.
- Rationale: Valid 2018 archive laps can lack a boolean personal-best flag; unknown and false are different facts.
- Date: 2026-07-28
- Status: implemented

### Curated schedule membership and exact missing-event hydration

- Decision: Use the curated public FastF1 schedule as the championship membership and round-number authority. Match private-index events strictly by normalized name and retain their session boundaries. For a curated event absent from the private index, resolve every session and require exact F1 timing `session_info` start, end, and offset metadata; never estimate a duration.
- Rationale: The cached 2026 private index assigned round 6 to both Miami and Monaco, and the cached 2018 private index omitted the Australian Grand Prix entirely. The curated schedule corrects membership/rounds but lacks end boundaries, while exact per-session timing metadata supplies them safely.
- Date: 2026-07-28
- Status: implemented

### Database-bound archive session identity

- Decision: Start one-session archive ingestion from a database `session_id`, derive the FastF1 request from its stored season, championship round, and upstream session name, verify the loaded identity, and revalidate the target identity under row locks before replacement.
- Rationale: Prevent caller-supplied identity drift or a concurrent target metadata change from writing one FastF1 session's data into another database session.
- Date: 2026-07-28
- Status: implemented

### Archive session attempt lifecycle

- Decision: Make pending idempotent without incrementing attempts; commit running before upstream work; increment attempts only when running begins; retain the latest sanitized failure until success; complete atomically with snapshot persistence; and record failure separately while preserving any previous completed snapshot.
- Rationale: Give the UI and future worker an accurate observable session state without holding database locks during FastF1 work or losing usable historical data after a failed refresh.
- Date: 2026-07-28
- Status: implemented

### Sanitized persisted failures

- Decision: Persist only stable error codes and fixed bounded messages selected by exception category; never persist raw exception text, causes, tracebacks, request representations, or upstream responses.
- Rationale: Raw failures can contain tokens, authorization headers, URLs, cookies, local paths, SQL parameters, or other sensitive operational data.
- Date: 2026-07-28
- Status: implemented

### Backfill retry and backoff policy

- Decision: Allow four attempts per job-session; classify only FastF1 loading, target changes, and confirmed transient database failures as retryable; use exponential backoff with a 60-second base, multiplier 2, 15-minute cap, and equal jitter from 50% to 100% of nominal delay.
- Rationale: Recover from transient upstream and infrastructure faults without repeatedly retrying deterministic configuration, normalization, identity, source, integrity, or unknown failures.
- Date: 2026-07-28
- Status: accepted

### Runtime policy primitives

- Decision: Represent the accepted runtime values in one immutable validated settings object, classify retries from original in-process exceptions, and calculate equal-jitter schedules as a pure function of injected PostgreSQL time and a caller-provided jitter fraction.
- Rationale: Keep policy configuration fail-fast and make retry behavior deterministic to test before it is connected to transactional job-state transitions.
- Date: 2026-07-28
- Status: implemented

### Transactional backfill claim and failure synchronization

- Decision: Claim one eligible job-session with `FOR UPDATE SKIP LOCKED`, atomically change the parent job, job-session, and persistent session ingestion to running, and return both the job attempt count and monotonic session-attempt token. Retry and terminal failure transitions must lock and validate both ownership values before updating the two session states together.
- Rationale: Prevent duplicate worker ownership, keep UI-facing job progress aligned with persistent ingestion state, and stop stale workers from recording failure over a newer attempt.
- Date: 2026-07-28
- Status: implemented

### Heartbeat and completion fencing

- Decision: Refresh parent-job, job-session, and persistent-session heartbeats in one ownership-validated transaction. When a claim is supplied to archive persistence, lock and validate its job attempt and monotonic session token before any sporting writes, then complete the job-session and persistent session atomically with the replacement snapshot.
- Rationale: Make lease evidence internally consistent and prevent a delayed stale worker from overwriting a newer attempt or completing job progress after ownership is lost.
- Date: 2026-07-28
- Status: implemented

### Stale-lease recovery

- Decision: Recover a bounded oldest-first batch of running job-sessions whose job-session and persistent-session heartbeat evidence are older than the configured lease. Lock candidates with `FOR UPDATE SKIP LOCKED`, preserve previous successful archive metadata, apply fixed lease-expiry diagnostics, and use the normal retry budget/backoff without incrementing attempt counters during recovery.
- Rationale: Make abandoned work retryable without duplicate scanners processing the same row, while immediately fencing the lost worker through the synchronized state change.
- Date: 2026-07-28
- Status: implemented

### Heartbeat, lease, and fencing policy

- Decision: Heartbeat every 30 seconds, expire leases after 5 minutes, scan for recovery every 30 seconds, and fence all heartbeat/failure/completion writes with job ID, job attempt count, and the monotonic session-ingestion attempt token.
- Rationale: Recover abandoned work while preventing a delayed stale worker from overwriting a newer attempt.
- Date: 2026-07-28
- Status: accepted

### Current-season freshness policy

- Decision: Use a 6-hour current-season coverage TTL, 30-day historical coverage TTL, 2-hour post-session archive grace period, and automatic archive correction checkpoints at 24 hours and 7 days after scheduled session end.
- Rationale: Serve existing data immediately, discover new current-season sessions promptly, avoid requesting archives before they are likely available, and capture normal post-session corrections without indefinite upstream polling.
- Date: 2026-07-28
- Status: accepted

### Freshness eligibility evaluation

- Decision: Evaluate coverage freshness from `coverage_valid_until` against PostgreSQL time, using the UTC calendar year to select the current or historical TTL. Evaluate initial archive eligibility at the exact grace boundary and correction eligibility from the latest due checkpoint not satisfied by `completed_at`; a completion at a checkpoint satisfies it.
- Rationale: Give future orchestration one deterministic, timezone-safe policy boundary while avoiding duplicate catch-up refreshes when a scan happens after multiple checkpoints.
- Date: 2026-07-28
- Status: implemented

### FastF1 schedule source boundary

- Decision: Discover 2018+ championship schedules through FastF1 3.8.3's cache-decorated F1 timing season index, require its real `StartDate`, `EndDate`, and `GmtOffset` fields, and serialize access with full session loading.
- Rationale: FastF1's public schedule frame drops session end timestamps, while archive grace and correction checkpoints require a truthful end boundary. Rejecting incomplete snapshots is safer than inventing session durations.
- Date: 2026-07-28
- Status: implemented

### Latest schedule membership

- Decision: Preserve events and sessions absent from a later schedule snapshot, but mark current rows with one `last_discovered_at` PostgreSQL timestamp and plan automatic work only from that latest membership.
- Rationale: Existing jobs and sporting data can reference removed rows, while canceled or removed sessions must not remain automatically eligible.
- Date: 2026-07-28
- Status: implemented

### Transactional season backfill planning

- Decision: Load a needed schedule outside database locks, then recheck freshness and atomically persist coverage plus create/reuse the active job under a transaction-level season advisory lock. Append only missing eligible job-session rows.
- Rationale: Avoid holding database locks during upstream work, make concurrent year requests converge on one active job, and retain the partial unique index as a final database defense.
- Date: 2026-07-28
- Status: implemented

### Parent-job aggregation

- Decision: Lock every job-session in deterministic session order before its parent job. Keep unstarted work pending, keep started jobs running while any child remains pending/running, complete only when every child completed, and fail only when all children are terminal and at least one failed. Preserve terminal status/timestamp on repeated aggregation and use one fixed parent failure diagnostic instead of copying child errors.
- Rationale: Prevent terminal parent decisions from racing child transitions, preserve a monotonic UI-facing lifecycle through retry backoff, and expose safe progress counts without leaking operational failure details.
- Date: 2026-07-28
- Status: implemented

### Single-concurrency archive worker

- Decision: Poll every two seconds while idle, process only one FastF1 session at a time, heartbeat the active claim every 30 seconds in a dedicated thread, and run stale-lease recovery plus active-parent reconciliation immediately at startup and every 30 seconds. Stop taking new claims on SIGINT/SIGTERM while allowing the active in-process attempt to finish, use a two-minute local Compose stop grace period, and log exception types without raw exception text.
- Rationale: Respect upstream/cache limits, keep blocking FastF1 work lease-safe, repair terminal parent state after transient failures or restarts, and shut down without intentionally abandoning owned work.
- Date: 2026-07-28
- Status: implemented

### Manual backfill cancellation scope

- Decision: Defer manual job cancellation from the historical MVP. Retain only `pending`, `running`, `completed`, and `failed` states; add no cancellation endpoint or migration. Worker shutdown remains the operational escape hatch, and cancellation will be reconsidered after real duration measurements or before production or multi-user operation requires it.
- Rationale: A synchronous FastF1 attempt has no safe interruption boundary, while graceful shutdown and lease recovery already cover the operational need without introducing partially defined state transitions.
- Date: 2026-07-28
- Status: accepted

### Historical REST API contract

- Decision: Use `/api/v1` for historical product endpoints while retaining `/api/health`; keep season and job GET operations read-only; use a separate idempotent POST backfill command; serialize database `BIGINT` values as decimal strings; normalize timestamps to UTC; and expose only stable client-safe errors.
- Rationale: Separate commands from reads, protect JavaScript clients from integer precision loss, and provide one explicit, versioned contract for the web dashboard and future iOS client.
- Date: 2026-07-28
- Status: accepted

### Historical API foundation

- Decision: Define strict Pydantic response/error models, supported-year validation, the empty `/api/v1` router boundary, and derived season-status precedence before implementing database read services or route handlers.
- Rationale: Make contract and policy behavior independently testable and prevent route handlers from becoming the business-rule boundary.
- Date: 2026-07-28
- Status: implemented

### Season overview read consistency

- Decision: Build the season overview inside one PostgreSQL `REPEATABLE READ, READ ONLY` transaction, use the PostgreSQL timestamp for every freshness decision, and include only event/session rows whose discovery marker equals the season's latest successful coverage timestamp.
- Rationale: Produce one internally consistent response, make accidental writes fail at the database boundary, and keep removed or cancelled calendar rows stored without presenting them as current.
- Date: 2026-07-28
- Status: implemented

### Season overview HTTP boundary

- Decision: Expose `GET /api/v1/seasons/{season_year}` as a read-only route with dependency-injected database sessions, `Cache-Control: no-store`, the strict season overview response model, and stable client-safe `422`, `500`, and `503` failures.
- Rationale: Keep transport concerns thin, preserve the database service as the business/read-model boundary, prevent mutable progress data from being cached, and avoid leaking configuration or database diagnostics.
- Date: 2026-07-28
- Status: implemented

### Job-progress read consistency

- Decision: Read a backfill job and every child session inside one PostgreSQL `REPEATABLE READ, READ ONLY` transaction, derive progress counts from those child rows, order them deterministically by round and scheduled session time, and never aggregate or mutate parent state during a read.
- Rationale: Give web and future iOS clients one internally consistent progress snapshot while preserving strict command/read separation and preventing polling from changing job lifecycle state.
- Date: 2026-07-28
- Status: implemented

### Job-progress HTTP boundary

- Decision: Expose `GET /api/v1/backfill-jobs/{job_id}` as a read-only route with FastAPI UUID validation, dependency-injected database sessions, `Cache-Control: no-store`, the strict job-progress response model, and stable client-safe `404`, `500`, and `503` failures.
- Rationale: Let web and future iOS clients poll progress through a thin transport layer without causing aggregation, writes, or leakage of internal diagnostics.
- Date: 2026-07-28
- Status: implemented

### Backfill command HTTP boundary

- Decision: Expose `POST /api/v1/seasons/{season_year}/backfill` without a request body; delegate to `ensure_season_backfill`; return `202` plus `Location` and `Retry-After: 2` when a job exists, otherwise `200`; and map planner, schedule, cache, and database failures to the accepted stable error contract.
- Rationale: Complete the explicit command/read separation, retain planner idempotency and advisory-lock guarantees, and give clients one predictable transition from season selection to progress polling.
- Date: 2026-07-28
- Status: implemented

### API schedule-cache access

- Decision: Mount the persistent FastF1 cache volume into both API and worker containers. The API uses it for synchronous season schedule discovery only; archive session ingestion remains worker-only.
- Rationale: The accepted backfill command may need schedule coverage before it can queue work, while session ingestion must remain outside the request lifecycle.
- Date: 2026-07-28
- Status: implemented

## Database Model

Alembic revision `20260727_0001` implements the backfill control plane:

- `seasons`: One row per season. `year` is a `SMALLINT` primary key constrained to `>= 1950`. Coverage timestamps support future freshness derivation.
- `events`: Championship events keyed internally by `BIGINT IDENTITY`, linked to `seasons`, unique by `(season_year, round_number)`, and marked for latest schedule membership by `last_discovered_at`.
- `sessions`: Event sessions keyed internally by `BIGINT IDENTITY`, linked to `events`, unique by `(event_id, session_key)`, and marked for latest schedule membership by `last_discovered_at`.
- `session_ingestions`: One persistent ingestion-state row per session, including status, source, provisional/finalized state, attempts, lifecycle timestamps, retry eligibility, heartbeat, and sanitized error fields. Direct managed archive attempts, orchestrated claims/failures, ownership-fenced heartbeat updates, claim-aware completion, and stale-lease recovery are implemented.
- `backfill_jobs`: UUID year-level jobs linked to a season. A partial unique index on `season_year` for `pending` and `running` rows prevents two active jobs for one year. Transactional aggregation from locked job-session rows is implemented.
- `backfill_job_sessions`: Per-job, per-session progress with composite primary key `(job_id, session_id)`, attempt and lifecycle fields, and worker-claim/progress indexes.

Implemented constraints and indexes:

- Foreign keys restrict deletion from seasons through sessions and ingestion state.
- Deleting a backfill job cascades only to its `backfill_job_sessions`.
- Status values are `pending`, `running`, `completed`, or `failed`.
- Sources are `live_signalr`, `fastf1_archive`, or `jolpica`.
- Record states are `provisional` or `finalized`.
- Backfill request reasons are `missing`, `partial`, `stale`, or `manual`.
- Attempt counts must be non-negative.
- Deterministic names are generated for primary keys, foreign keys, unique constraints, checks, and indexes.

Alembic revision `20260728_0002` implements the accepted sporting-data schema:

- `drivers`: Global internal driver identity with nullable unique Jolpica and live-reference identifiers plus name and country fields.
- `session_entries`: Session-specific driver, racing-number, display, team, source, and finalization snapshots. Unresolved entries can have a null `driver_id`.
- `session_results`: One result per session entry with positions, points, completed laps, qualifying times, normalized elapsed time, leader gaps, source, and finalization state.
- `laps`: One lap summary per session entry and lap number with timing, pit, sector, speed-trap, tyre, track-status, position, deletion, generation, accuracy, source, and finalization fields.

Revision 2 constraints include:

- Unique non-null Jolpica driver IDs and live references.
- Unique `(session_id, entry_key)`.
- Partial uniqueness of non-null `(session_id, driver_id)` and `(session_id, racing_number)`.
- Unique `(session_entry_id, lap_number)`.
- Restricted foreign-key deletion.
- Named source, record-state, non-negative, and positive-value checks.

Alembic revision `20260728_0003` implements schedule discovery membership:

- Adds nullable `last_discovered_at` timestamps to `events` and `sessions`.
- Adds `(season_year, last_discovered_at)` and `(event_id, last_discovered_at)` lookup indexes.
- Invalidates existing non-null season coverage on upgrade so the next discovery refresh assigns authoritative membership.
- Preserves all existing calendar, job, ingestion, and sporting rows.

Alembic revision `20260728_0004` implements ingestion resilience:

- Adds the singleton `upstream_request_gates` coordination row for
  cross-process FastF1 archive pacing and rate-limit cooldown.
- Makes `laps.is_personal_best` nullable so unknown historical source values are
  preserved.
- Preserves all existing completed session and sporting data during upgrade.

The implemented Revision 2 model is documented in `docs/SPORTING_DATA_DESIGN.md`. No telemetry table exists.

Planned but not implemented behavior:

- Keep TimescaleDB and telemetry table shape outside the initial relational migrations.

## API Contract

Implemented endpoints:

### `GET /`

- Returns `200 OK`.
- Returns the API name and the current `scaffold` status.

### `GET /api/health/live`

- Returns `200 OK`.
- Reports process liveness without requiring a database connection.

### `GET /api/health/ready`

- Returns `200 OK` with database status `ready` when PostgreSQL is reachable.
- Returns `503 Service Unavailable` with database status `not_configured` when `DATABASE_URL` is missing.
- Returns `503 Service Unavailable` with database status `unavailable` when PostgreSQL cannot be reached.

### `GET /api/v1/seasons/{season_year}`

- Returns `200 OK` with the accepted season overview contract for supported years, including `status: "missing"` and an empty event list when no season coverage exists.
- Reads PostgreSQL only; it never contacts FastF1, starts a job, or writes data.
- Uses one repeatable-read, read-only database snapshot and only the latest successful schedule-discovery membership.
- Returns `Cache-Control: no-store`.
- Returns stable `422 season_year_out_of_range` for years before 2018 or after the current UTC year.
- Retains FastAPI's standard `422` validation response for a malformed integer path value.
- Returns stable, sanitized `500 server_configuration_error` and `503 database_unavailable` responses.

### `POST /api/v1/seasons/{season_year}/backfill`

- Accepts no request body and supports seasons from 2018 through the current UTC year.
- Returns `202 Accepted` with `Location: /api/v1/backfill-jobs/{job_id}` and `Retry-After: 2` when a job is created or reused.
- Returns `200 OK` when coverage is refreshed without eligible work or no action is needed.
- Returns the accepted `job_created`, `job_reused`, `coverage_refreshed`, or `no_action` response action.
- Delegates schedule refresh and idempotent job planning to `ensure_season_backfill`; it does not ingest a FastF1 session in the request.
- Returns stable, sanitized `409`, `500`, `502`, and `503` failures for planning conflicts, configuration, invalid snapshots, upstream availability, and database availability.

### `GET /api/v1/backfill-jobs/{job_id}`

- Returns `200 OK` with parent lifecycle details, derived progress counts, and every child session in deterministic round/session order.
- Reads PostgreSQL only and never runs parent aggregation or writes data.
- Returns `Cache-Control: no-store`.
- Returns FastAPI's standard `422` validation response for malformed UUIDs.
- Returns stable `404 backfill_job_not_found` for unknown UUIDs.
- Returns stable, sanitized `500 server_configuration_error` and `503 database_unavailable` responses.

No session-results, lap, telemetry, or WebSocket endpoint has been
implemented. The versioned `/api/v1` router, strict historical response/error
models, supported-year validation, pure derived season-status policy, read-only
season endpoint, read-only job-progress endpoint, and idempotent backfill command
are implemented.

The accepted first historical API contract is documented in
`docs/HISTORICAL_API_DESIGN.md`.

Accepted future behavior:

- Historical data will be served over REST.
- Live timing will be served over WebSocket.
- The backend will check ingestion coverage when a season is requested.
- Completed session data must remain queryable while the rest of a backfill is running.
- Season responses must not include all telemetry; telemetry will be queried by session, driver, and lap.

## Backfill Design

Accepted behavior:

1. A user selects a season/year in the dashboard.
2. The backend checks ingestion coverage for that season.
3. A fully ingested season is served directly from the database.
4. Missing, partial, or stale data causes a background backfill job to be started or reused.
5. A backfill is requested by year and processed by event/session.
6. Processing must be idempotent.
7. Two active jobs for the same year must not exist concurrently.
8. Session states are `pending`, `running`, `completed`, and `failed`.
9. Year states are `missing`, `pending`, `running`, `partial`, `completed`, `stale`, and `failed`.
10. The UI must be able to display job progress.
11. Completed sessions must be queryable while backfill continues.
12. The current season may be served while missing or new sessions are checked in the background.
13. FastF1 cache must be used, and aggressive parallel requests must be avoided.
14. Telemetry must be queried separately by session, driver, and lap.
15. Manual job cancellation is deferred from the historical MVP; no cancellation
    state or endpoint is added in this phase.

The accepted one-session replacement and attempt contract is documented in `docs/FASTF1_INGESTION_CONTRACT.md`. A managed archive attempt commits running state and increments its attempt count before calling the vertical slice. The slice derives one request from database session identity, loads through the persistent serialized cache, verifies loaded identity, normalizes results and laps, and atomically replaces the target archive snapshot. Success marks ingestion completed/finalized with the snapshot. Failure is re-raised after a separate owning-attempt transaction stores only a fixed sanitized code and message; a previous completed snapshot and its timestamps remain available. The runtime policy in `docs/BACKFILL_RUNTIME_POLICY.md` is accepted. Its validated settings, original-exception retry classification, retry-budget validation, deterministic equal-jitter schedule calculations, and pure freshness eligibility decisions are implemented. The orchestration layer atomically claims eligible job-session and persistent-session state, starts the parent job, increments the two distinct attempt counters, records an initial database-clock heartbeat, synchronizes retryable or terminal failures, and exposes an ownership-fenced heartbeat transaction. The one-session vertical slice accepts an optional claim and a pre-persistence heartbeat guard. Claim-aware persistence validates both ownership tokens before sporting writes and completes the job-session and persistent session in the same transaction as the archive snapshot. Bounded stale-lease recovery moves abandoned synchronized state to pending with normal backoff or to failed after attempt four, while preserving any prior completed snapshot and fencing the original worker. Parent aggregation locks child rows before the job, applies monotonic status precedence, preserves terminal timestamps, records fixed aggregate diagnostics, and returns all progress counts. The worker composes these operations sequentially, heartbeats active blocking work, runs recovery/active-parent maintenance, and handles graceful shutdown. The schedule contract in `docs/SCHEDULE_DISCOVERY_DESIGN.md` connects freshness decisions to a pinned FastF1 season-index snapshot, atomic current-membership persistence, and advisory-locked job creation/reuse. The POST backfill endpoint invokes this planner, and the resulting database work is claimable by the worker.

The worker claim boundary now locks the persistent `fastf1_archive` request gate
before session state and reserves the next archive start at least 90 seconds
later. An explicit FastF1 rate-limit failure returns the session to pending,
restores its job-session retry budget, retains the monotonic lifetime ingestion
token, and closes the shared gate for one hour. Schedule discovery retains raw
session boundaries for matched events, uses the curated schedule for complete
championship membership, and hydrates a missing event only through exact
per-session timing metadata. Historical laps with an unknown `IsPersonalBest`
value are stored as null.

## Live Timing Design

Live timing has not been implemented.

Accepted target behavior:

- SignalR data is stored as live/provisional data during a session.
- FastF1 archive data is used for finalization and reconciliation after the session.
- Data source identity is preserved as `live_signalr`, `fastf1_archive`, or `jolpica`.
- Web and future iOS clients communicate only with the backend.
- Tokens, cookies, credentials, and similar sensitive values are never sent to a client.

SignalR protocol details, connection lifecycle, message schemas, and reconciliation rules remain future design work.

## Completed Work

- Confirmed the project directory is writable and was initially empty.
- Created the persistent agent rules and project context.
- Created the local Docker Compose scaffold for PostgreSQL, API, worker, and frontend.
- Added FastAPI liveness and database readiness endpoints.
- Added the placeholder worker process and health check.
- Added the React/Vite readiness screen.
- Declared persistent PostgreSQL and FastF1 cache volumes.
- Verified the frontend production build.
- Verified Docker Compose configuration parsing.
- Created the proposed Alembic and database model design document.
- Accepted the phased database design, synchronous SQLAlchemy layer, one-shot migration service, derived season status, championship-round scope, and text-backed checked states.
- Added the SQLAlchemy metadata, connection layer, and synchronous session factory.
- Added Alembic configuration and Revision 1 for all six backfill control-plane tables.
- Added database metadata and PostgreSQL integration tests.
- Verified fresh upgrade, schema/metadata drift check, constraint enforcement, downgrade, re-upgrade, and a second no-op upgrade.
- Verified the full five-service Compose stack starts with healthy database, API, worker, and frontend services after migration completes.
- Inspected FastF1 3.8.3 result schemas across modern race, qualifying, sprint, 2018 race, and one pre-2018 boundary sample.
- Inspected 1,129 modern race lap rows and documented type, nullability, timing, tyre, speed, deletion, and accuracy behavior.
- Confirmed that racing numbers are not global driver identities and accepted a 2018 historical-ingestion boundary.
- Created the proposed Revision 2 sporting-data design based on the inspected data.
- Finalized all Revision 2 open design choices using the recommended options: nullable unresolved driver links, normalized result timing, full lap summaries, and race-control-message loading.
- Added SQLAlchemy models for `drivers`, `session_entries`, `session_results`, and `laps`.
- Added and verified Alembic revision `20260728_0002` for the sporting-data schema.
- Added metadata and PostgreSQL integration coverage for sporting-data constraints, nullable identities, session-scoped number reuse, and idempotent natural-key upserts.
- Verified Revision 2 fresh upgrade, downgrade to Revision 1, re-upgrade, second no-op upgrade, Alembic head, and zero model/schema drift.
- Verified the full five-service Compose stack remains healthy with Revision 2 applied.
- Accepted and documented the one-session FastF1 archive replacement contract, including stale-row removal, rollback behavior, live-data protection, and deterministic fallback entry keys.
- Added FastF1 3.8.3 and pandas as locked backend runtime dependencies.
- Implemented pure results-and-laps normalization with immutable output records and validation for nulls, identifiers, integers, durations, decimals, booleans, speeds, result timing, lap association, and natural-key duplicates.
- Added focused normalization unit tests and verified the complete 23-test suite against an isolated PostgreSQL 17 Compose database.
- Implemented transaction-owning persistence for one normalized FastF1 archive snapshot with target-session row locking, non-archive ownership protection, natural-key upserts, bounded lap batches, stale-row deletion, and atomic ingestion completion.
- Added PostgreSQL integration coverage for stable idempotent IDs, stale replacement, fallback-key transitions, non-archive parent, child, and ingestion-state protection, constraint-failure rollback, and transaction ownership.
- Verified the complete 31-test suite against an isolated PostgreSQL 17 Compose database.
- Inspected the locked FastF1 3.8.3 cache, session construction, and session loading APIs.
- Implemented a deterministic cache-backed one-session loader with process-local serialization, race-control messages enabled, and telemetry and weather disabled.
- Added loader tests for cache lifecycle, exact FastF1 flags, request validation, environment configuration, failure wrapping, required tables, and concurrent-call serialization without upstream network access.
- Verified the complete 44-test suite against an isolated PostgreSQL 17 Compose database.
- Implemented a database-bound one-session vertical slice that derives the FastF1 request from stored session identity, validates the loaded identity, normalizes the candidate snapshot, and invokes transactional replacement.
- Added PostgreSQL integration coverage for vertical-slice idempotency, stable natural-key rows, target-derived requests, missing or concurrently changed targets, loaded identity mismatches, and no-write loading and normalization failures.
- Verified the complete 50-test suite against an isolated PostgreSQL 17 database.
- Implemented managed archive pending, running, completed, and failed session-ingestion transitions with attempt counting and overlap/source protection.
- Implemented fixed exception-category error codes and secret-free persisted failure messages while re-raising original exceptions.
- Added PostgreSQL coverage for observable running state, idempotent pending, attempt increments, prior-snapshot preservation, sanitized failure fields, existing-running rejection, non-archive ownership, and timestamp validation.
- Added focused coverage for every stable persisted failure-code mapping and its fixed secret-free message.
- Verified the complete 66-test suite against an isolated PostgreSQL 17 database.
- Diagnosed the VS Code unresolved-import issue as a Linux-container virtual environment being reused on macOS while the host had only Python 3.9.
- Installed host Python 3.13 and `uv`, recreated the ignored `backend/.venv` natively from `uv.lock`, and verified SQLAlchemy, pandas, FastF1, Ruff, and focused pytest execution.
- Created and accepted the runtime policy for retry budgets, backoff, heartbeat, lease recovery, stale-worker fencing, and current-season freshness.
- Implemented immutable validated runtime settings with typed environment overrides and duration accessors.
- Implemented retryable/terminal classification from original FastF1, archive, SQLAlchemy, and PostgreSQL failure categories.
- Implemented deterministic equal-jitter backoff scheduling with retry-budget, timezone, and jitter validation.
- Added 46 focused runtime-policy tests and verified the complete 112-test suite against an isolated PostgreSQL 17 database.
- Implemented transactional `FOR UPDATE SKIP LOCKED` job-session claiming synchronized with parent-job and persistent session-ingestion running state.
- Implemented retryable and terminal failure transitions with shared database-clock retry timestamps, fixed sanitized diagnostics, four-attempt exhaustion, and job/session attempt-token fencing.
- Added 11 PostgreSQL orchestration tests and verified the complete 123-test suite against an isolated PostgreSQL 17 database.
- Implemented ownership-fenced heartbeat transactions that refresh parent-job, job-session, and persistent-session timestamps together.
- Implemented optional claim-aware archive persistence and vertical-slice completion that validate both ownership tokens and complete both session states atomically with sporting data.
- Added 8 PostgreSQL heartbeat/completion tests and verified the complete 131-test suite against an isolated PostgreSQL 17 database.
- Implemented bounded oldest-first stale-lease recovery with `FOR UPDATE SKIP LOCKED`, fixed diagnostics, normal retry backoff, fourth-attempt exhaustion, and completed-session preservation.
- Added 10 PostgreSQL lease-recovery and resumed-stale-worker tests and verified the complete 141-test suite against an isolated PostgreSQL 17 database.
- Implemented pure UTC-aware season-coverage and archive-ingestion eligibility decisions for coverage TTLs, the post-session grace period, correction checkpoints, late scans, and stable archives.
- Added 24 focused freshness-policy tests and verified the complete 165-test suite against an isolated PostgreSQL 17 database.
- Implemented transactional parent-job aggregation with deterministic child-before-parent locking, monotonic status precedence, progress counts, fixed failure diagnostics, and terminal idempotency.
- Added 13 PostgreSQL aggregation tests, including row-lock serialization, and verified the complete 178-test suite against an isolated PostgreSQL 17 database.
- Implemented the single-concurrency archive worker with configurable idle polling, periodic heartbeat and recovery scheduling, fenced success/failure handling, active-parent reconciliation, secret-safe logs, pre-persistence heartbeat guarding, and graceful shutdown.
- Added 13 worker/runtime/PostgreSQL tests with controlled FastF1 doubles, including heartbeat advancement, retryable and terminal outcomes, recovered-lease fencing, maintenance aggregation, startup maintenance, initialization failure hygiene, shutdown-before-claim fencing, and active-attempt shutdown; verified the complete 191-test suite against an isolated PostgreSQL 17 database.
- Verified the real Compose database/migration/worker startup, worker health check and idle behavior, and clean SIGTERM shutdown without an upstream request.
- Implemented pure normalization of FastF1 3.8.3 season-index meetings with real UTC session start/end timestamps, championship-round filtering, canonical future-safe session keys, and strict snapshot validation.
- Implemented serialized cache-backed schedule loading through FastF1's pinned F1 timing season index without a live upstream test request.
- Added Alembic Revision 3 discovery markers and indexes so removed calendar rows remain stored but are excluded from latest-snapshot planning.
- Implemented atomic calendar refresh and advisory-locked season planning that rechecks freshness, reuses one active job, and queues only missing eligible sessions.
- Added 23 schedule/planner/schema tests, including concurrent job reuse, worker claim compatibility, removed-session preservation, correction planning, source-conflict rollback, and empty-job prevention; verified the complete 214-test suite against PostgreSQL 17.
- Verified Revision 3 upgrade, downgrade to Revision 2, re-upgrade, Alembic head, and zero model/schema drift.
- Accepted deferral of manual backfill cancellation from the historical MVP without changing the existing lifecycle states or database schema.
- Created and accepted the first historical season and backfill REST API contract.
- Implemented the empty `/api/v1` router boundary, strict historical Pydantic response/error contracts, supported-year validation, UTC timestamp normalization, decimal-string database identifiers, and pure derived season-status precedence.
- Added 36 focused API-foundation and health tests; verified the complete 247-test backend suite against a fresh isolated PostgreSQL 17 database.
- Implemented the read-only season overview database service with repeatable-read snapshot consistency, latest-discovery membership, session eligibility and data-availability mapping, aggregate counts, active-job summary, deterministic ordering, and derived status.
- Added 6 season-overview policy and PostgreSQL integration tests; verified the complete 253-test backend suite against a fresh isolated PostgreSQL 17 database.
- Implemented `GET /api/v1/seasons/{season_year}` with supported-year validation, dependency-injected database access, no-store caching, strict response/OpenAPI schemas, and sanitized configuration/database failure mappings.
- Added `httpx2` as a development-only dependency for Starlette's current test client and added route, error, and OpenAPI coverage; verified the complete 261-test backend suite against a fresh isolated PostgreSQL 17 database.
- Implemented the read-only job-progress database service with repeatable-read snapshot consistency, dedicated not-found behavior, parent and child lifecycle mapping, derived progress counts, deterministic round/session ordering, and no aggregation side effects.
- Added 8 job-progress policy and PostgreSQL integration tests; verified the complete 269-test backend suite against a fresh isolated PostgreSQL 17 database.
- Implemented `GET /api/v1/backfill-jobs/{job_id}` with UUID validation, dependency-injected database access, no-store caching, strict response/OpenAPI schemas, and sanitized not-found, configuration, and database failure mappings.
- Added 6 job-progress endpoint tests; verified the complete 275-test backend suite against a fresh isolated PostgreSQL 17 database.
- Implemented `POST /api/v1/seasons/{season_year}/backfill` with dynamic `200/202` behavior, polling headers, stable action mapping, supported-year validation, and sanitized planner/schedule/cache/database failures.
- Mounted the persistent FastF1 cache into the API for synchronous schedule discovery while retaining worker-only archive ingestion.
- Added 15 focused command-endpoint tests and one concurrent HTTP-to-planner-to-worker integration test; verified the complete 291-test backend suite against a fresh isolated PostgreSQL 17 database.
- Verified local Compose database, migration, API, and worker startup; API/worker health; API cache-path configuration; and the shared persistent cache mount without an upstream request.
- Updated the repository README to describe the implemented historical API slice and its local endpoints.
- Replaced the frontend readiness scaffold with a responsive season archive dashboard using typed API contracts and no new package dependencies.
- Added current-UTC-year season selection, API readiness state, coverage and count cards, a segmented ingestion progress visualization, event/session status cards, empty/loading/error states, and accessible local controls.
- Connected the dashboard to season reads, idempotent backfill commands, and two-second active-job polling while preserving backend-only FastF1 access and excluding telemetry.
- Verified the frontend production build, Compose configuration, healthy local five-service startup from existing images, served dashboard metadata, and the live missing-season overview response.
- Diagnosed the first real 2018 backfill from service logs and database state: 51 sessions completed, 13 sessions failed on missing historical personal-best flags, and 36 sessions exhausted retries after FastF1's hourly request limiter opened.
- Added Alembic Revision 4 with a persistent FastF1 request gate and nullable historical lap personal-best flags.
- Implemented cross-worker 90-second archive pacing, a distinct secret-free FastF1 rate-limit failure, one-hour global cooldown, and job-session retry-budget preservation.
- Implemented strict 2026 duplicate-round reconciliation using the curated public schedule for round numbers while retaining private-index session boundaries.
- Verified 303 backend tests against an isolated PostgreSQL 17 database, Revision 4 downgrade/re-upgrade, local development migration, healthy services, and preservation of all 51 completed sessions and 23,928 stored laps.
- Diagnosed the missing 2018 Australian Grand Prix as an omission in FastF1's private F1 timing season index rather than a dashboard, API, or persistence defect.
- Made the curated FastF1 schedule authoritative for complete championship membership and added exact per-session metadata hydration for curated events missing from the private index.
- Verified the corrected real cached 2018 loader returns 21 events and exact boundaries for all five Australian sessions; verified the complete 304-test backend suite.
- Atomically repaired the local 2018 schedule snapshot without creating a job; the live API now returns 21 events beginning with Australian Grand Prix and 105 sessions.

No historical result/lap detail view, telemetry feature, or live timing feature has been completed.

## Work in Progress

- No development change is currently in progress.

## Next Steps

1. Decide the semantics and API/UI contract for an observable local FastF1
   request-budget estimate without presenting it as an authoritative upstream
   quota.
2. Start a new idempotent 2018 backfill request when the user is ready and monitor its deliberately paced recovery; the failed pre-fix job remains immutable history.
3. Design historical session result and lap-summary read contracts before adding their API and UI views.
4. Measure telemetry volume before deciding on TimescaleDB.
5. Design SignalR live timing and reconciliation separately.

## Run and Test Commands

Verified:

```bash
npm run build --prefix frontend
docker compose config --quiet
docker compose up --detach
docker compose up --build --detach
docker compose up --build --detach db migrate worker
docker compose stop worker
docker compose run --rm migrate /opt/venv/bin/alembic upgrade head
docker compose run --rm migrate /opt/venv/bin/alembic current
docker compose run --rm migrate /opt/venv/bin/alembic check
docker compose run --rm migrate /opt/venv/bin/alembic downgrade 20260728_0002
docker compose run --rm migrate /opt/venv/bin/alembic downgrade 20260727_0001
docker compose run --rm migrate /opt/venv/bin/alembic downgrade base
```

Backend lint and tests are verified in the pinned uv container without using the ignored host virtual environment:

```bash
docker run --rm -e UV_PROJECT_ENVIRONMENT=/tmp/formula1-dashboard-venv -v "$PWD/backend:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.11.29-python3.13-trixie-slim uv run --frozen ruff check .
docker run --rm -e UV_PROJECT_ENVIRONMENT=/tmp/formula1-dashboard-venv -v "$PWD/backend:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.11.29-python3.13-trixie-slim uv run --frozen pytest
```

Host-side VS Code and Python tooling were verified on macOS with:

```bash
brew install python@3.13 uv
cd backend
uv venv --clear --python 3.13 .venv
uv sync --frozen
.venv/bin/ruff check .
.venv/bin/pytest tests/test_archive_attempt.py
```

Database integration tests additionally require `TEST_DATABASE_URL` and a migrated PostgreSQL database. The complete suite passed with 304 tests against an isolated PostgreSQL 17 database after curated-membership and exact missing-event hydration coverage were added.

## Known Issues and Technical Debt

- Loader tests use controlled FastF1 doubles and do not perform an upstream network smoke test.
- Schedule discovery combines FastF1 3.8.3's curated public schedule with its private, cache-decorated F1 timing APIs because neither source alone supplies both complete membership and exact end timestamps. The exact FastF1 pin and focused contract tests contain that compatibility risk.
- Failure recording requires a separate database transaction; if the database is unavailable, the original exception is re-raised with a diagnostic note but failed state cannot be persisted.
- Session-row locking serializes callers that use persistence, recovery, aggregation, and worker services.
- FastF1 loading and cache activation remain serialized only within each process. Archive session starts are now cross-process paced through PostgreSQL, but API schedule discovery and cache file access do not yet share a cross-process mutex.
- The failed pre-fix 2018 job remains terminal history with 51 completed and 49 failed sessions. Its completed data is preserved; resuming the missing sessions requires a new idempotent backfill request.
- TimescaleDB usage has not been decided.
- Recovery deliberately skips inconsistent rows whose persistent session is missing, owned by another source, completed, non-running, or has a fresh heartbeat. Such rows can remain running at job-session level until a future reconciliation policy is implemented.
- The worker does not invoke season planning; the implemented POST backfill command must run before newly eligible rows can be processed.
- Graceful shutdown waits for active in-process FastF1 work; local Compose allows two minutes before forced termination, after which lease recovery applies.
- Manual job cancellation is intentionally deferred from the historical MVP.
- The accepted first historical season/backfill API slice is implemented. Historical session results and lap-summary read contracts remain undesigned.
- The first dashboard has build and live local smoke coverage but no dedicated frontend unit or browser interaction test suite yet.
- Docker registry metadata timed out during the latest image rebuild attempt; the existing images and bind-mounted source started successfully, and the local dashboard/API health checks passed.
- FastF1 ingestion time and storage volume have not been measured.
- Live SignalR protocol and reconciliation rules have not been designed.
- PostgreSQL trust authentication is suitable only for the current loopback-bound local environment.
- Virtual environments are platform-specific. Never reuse a `.venv` created inside the Linux container on macOS; recreate the ignored host environment from `uv.lock`.

## Important Files

- `AGENTS.md`: Mandatory context, safety, language, and user-change preservation rules.
- `docs/PROJECT_CONTEXT.md`: Authoritative record of current behavior, accepted decisions, and next steps.
- `docs/BACKFILL_RUNTIME_POLICY.md`: Accepted runtime retry, backoff, heartbeat, lease recovery, fencing, and freshness decisions.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic layout, relational model, migration phases, idempotency, locking, and recovery design.
- `docs/FASTF1_INGESTION_CONTRACT.md`: Accepted one-session archive snapshot identity, validation, atomic replacement, and failure behavior.
- `docs/HISTORICAL_API_DESIGN.md`: Accepted and implemented first historical season/backfill API contract.
- `backend/app/api/backfill_job.py`: Repeatable-read, database-read-only job and child-session mapping with derived counts, deterministic ordering, and dedicated not-found behavior.
- `backend/app/api/backfill_jobs.py`: Read-only job-progress HTTP route, UUID validation, response contract, no-store policy, and sanitized failure mappings.
- `backend/app/api/contracts.py`: Strict historical API response/error models, enum values, UTC timestamps, decimal-string identifiers, and cross-field validation.
- `backend/app/api/dependencies.py`: Supported 2018-through-current-UTC-year validation, database and schedule-loader injection, and stable API dependency errors.
- `backend/app/api/errors.py`: Stable client-safe FastAPI error envelope.
- `backend/app/api/router.py`: Mounted `/api/v1` router boundary for historical product endpoints.
- `backend/app/api/season_overview.py`: Repeatable-read, database-read-only latest-membership season overview, eligibility/count mapping, active-job summary, and derived status.
- `backend/app/api/seasons.py`: Season overview and backfill-command routes, dynamic command response mapping, polling headers, and sanitized failure mappings.
- `backend/app/api/season_status.py`: Pure validated derived season-status precedence.
- `docs/SCHEDULE_DISCOVERY_DESIGN.md`: Implemented FastF1 schedule source, normalized snapshot, atomic membership persistence, and season job-planning contract.
- `docs/SPORTING_DATA_DESIGN.md`: Implemented Revision 2 schema, FastF1 inspection evidence, normalization rules, and decisions.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `backend/alembic/versions/20260727_0001_backfill_control_plane.py`: Reviewed Revision 1 schema and downgrade.
- `backend/alembic/versions/20260728_0002_sporting_data.py`: Reviewed Revision 2 sporting-data schema and downgrade.
- `backend/alembic/versions/20260728_0003_schedule_discovery.py`: Revision 3 schedule membership markers, coverage invalidation, indexes, and downgrade.
- `backend/alembic/versions/20260728_0004_ingestion_resilience.py`: Revision 4 FastF1 request-gate, historical personal-best nullability, seed data, and downgrade.
- `backend/app/db/base.py`: Shared SQLAlchemy metadata and timestamp mixin.
- `backend/app/db/models/`: Revision 1 control-plane and Revision 2 sporting-data SQLAlchemy models.
- `backend/app/db/models/request_gate.py`: Persistent cross-worker FastF1 archive pacing and rate-limit cooldown state.
- `backend/app/ingestion/archive_attempt.py`: Pending/running/failed archive attempt transitions, attempt counting, overlap protection, and fixed sanitized failure mappings.
- `backend/app/ingestion/backfill_worker.py`: Single-concurrency claim/execution loop, heartbeat monitor, recovery/aggregation maintenance, fixed outcome handling, and graceful shutdown.
- `backend/app/ingestion/backfill_orchestration.py`: Transactional job-session claiming, synchronized persistent state, fenced heartbeat updates, retry/terminal failure transitions, stale-lease recovery, parent-job aggregation, and ownership-token validation.
- `backend/app/ingestion/archive_ingestion.py`: Database-target lookup, FastF1 request derivation, loaded-identity validation, normalization, and persistence composition.
- `backend/app/ingestion/fastf1_loader.py`: Deterministic, cache-backed, process-serialized one-session FastF1 loading.
- `backend/app/ingestion/fastf1_schedule.py`: Curated championship membership, private-index reconciliation, exact missing-event timing hydration, and pure strict normalization.
- `backend/app/ingestion/season_backfill.py`: Atomic latest-snapshot persistence and advisory-locked active-job creation/reuse.
- `backend/app/ingestion/fastf1_normalization.py`: Pure FastF1 results-and-laps normalization and validation.
- `backend/app/ingestion/freshness_policy.py`: Pure UTC season-coverage, archive-grace, and correction-checkpoint eligibility decisions.
- `backend/app/ingestion/archive_persistence.py`: Atomic normalized archive upserts, stale-row replacement, source/identity guards, optional claim fencing, and synchronized completion.
- `backend/app/ingestion/runtime_policy.py`: Validated runtime settings, retry classification, SQLSTATE handling, and deterministic equal-jitter retry schedules.
- `backend/tests/test_archive_attempt.py`: Stable failure-code and fixed secret-free message mapping coverage.
- `backend/tests/test_api_contracts.py`: Strict API model, UTC timestamp, progress-count, and response consistency tests.
- `backend/tests/test_api_foundation.py`: Versioned router, existing-path preservation, year-boundary, and stable error tests.
- `backend/tests/test_backfill_job.py`: PostgreSQL job-progress mapping, ordering, sanitized errors, not-found, read-only transaction, and no-aggregation tests.
- `backend/tests/test_backfill_job_endpoint.py`: Job-progress route response, UUID validation, sanitized failure, caching, and OpenAPI tests.
- `backend/tests/test_season_overview.py`: PostgreSQL missing-season, latest-membership, preserved-snapshot, eligibility, ordering, and read-only transaction tests.
- `backend/tests/test_season_endpoint.py`: Season route response, year validation, sanitized failure, caching, and OpenAPI tests.
- `backend/tests/test_season_backfill_endpoint.py`: Backfill command actions, dynamic status/headers, supported-year handling, sanitized failure mappings, dependency behavior, and OpenAPI coverage.
- `backend/tests/test_season_backfill_endpoint_integration.py`: Concurrent POST idempotency, single-job persistence, worker claimability, and progress-read integration coverage.
- `backend/tests/test_season_status.py`: Derived season-status precedence and input-validation tests.
- `backend/tests/test_backfill_orchestration.py`: PostgreSQL claim locking, heartbeat synchronization, retry, lease recovery, parent aggregation, row-lock serialization, source protection, rollback, and ownership-token coverage.
- `backend/tests/test_archive_persistence.py`: PostgreSQL transactional persistence, idempotency, stale replacement, source protection, and rollback coverage.
- `backend/tests/test_archive_ingestion.py`: PostgreSQL one-session vertical-slice identity, idempotency, and pre-persistence failure coverage.
- `backend/tests/test_database_integration.py`: PostgreSQL constraint and index integration coverage.
- `backend/tests/test_sporting_data_integration.py`: Revision 2 identity, constraint, number-reuse, nullable-field, and idempotency integration coverage.
- `backend/tests/test_fastf1_loader.py`: FastF1 loader cache, configuration, flags, errors, and serialization tests.
- `backend/tests/test_fastf1_normalization.py`: FastF1 normalization happy-path and rejection coverage.
- `backend/tests/test_fastf1_schedule.py`: Schedule source, cache, serialization, curated membership, missing-event hydration, UTC boundary, canonical-key, and rejection tests.
- `backend/tests/test_season_backfill.py`: PostgreSQL calendar persistence, freshness, job reuse, concurrency, cancellation preservation, and rollback tests.
- `backend/tests/test_freshness_policy.py`: Coverage TTL, UTC year, exact grace/checkpoint, late-scan, stability, and timestamp validation coverage.
- `backend/tests/test_runtime_policy.py`: Runtime setting, environment parsing, retry classification, and backoff boundary coverage.
- `backend/tests/test_worker.py`: Worker startup maintenance, shutdown behavior, heartbeat interval validation, and secret-safe logging coverage.
- `backend/app/main.py`: FastAPI scaffold and health endpoints.
- `backend/app/worker.py`: Archive worker process setup, database/configuration readiness, signal handling, and readiness-file lifecycle.
- `backend/tests/test_health.py`: Backend health endpoint unit tests.
- `frontend/src/App.tsx`: Season selection, coverage metrics, progress visualization, event/session states, backfill command, and active-job polling UI.
- `frontend/src/api.ts`: Typed same-origin API client with stable safe error handling.
- `frontend/src/contracts.ts`: TypeScript representation of the implemented historical API response contracts.
- `frontend/src/index.css`: Responsive motorsport-inspired dashboard layout and visual state system.
- `README.md`: Local development overview and commands.

## Change Log

- 2026-07-28 — Reviewed and approved the curated-membership fix for the missing 2018 Australian Grand Prix.
- 2026-07-28 — Fixed the missing 2018 Australian Grand Prix by combining curated membership with exact F1 timing metadata and repaired the local schedule snapshot.
- 2026-07-28 — Diagnosed the real backfill failures and implemented verified global FastF1 pacing/cooldown, historical lap-null handling, and strict duplicate-round reconciliation.
- 2026-07-28 — Implemented and verified the first season archive dashboard with coverage, event/session, command, and job-progress visualization.
- 2026-07-28 — Implemented and verified the idempotent backfill command, API schedule-cache mount, and API-to-worker database handoff; prioritized the first dashboard visualization.
- 2026-07-28 — Implemented and verified the read-only job-progress HTTP endpoint with UUID validation and sanitized failures.
- 2026-07-28 — Implemented and verified the read-only job-progress database service with consistent derived counts and deterministic session ordering.
- 2026-07-28 — Implemented and verified the read-only season overview HTTP endpoint with strict contracts and sanitized failures.
- 2026-07-28 — Implemented and verified the read-only season overview service with repeatable snapshot consistency and latest-discovery membership.
- 2026-07-28 — Implemented and verified the versioned historical API foundation, strict response/error contracts, supported-year validation, and pure derived season-status policy.
- 2026-07-28 — Accepted the first versioned historical season and backfill REST API contract for incremental implementation.
- 2026-07-28 — Deferred manual cancellation from the historical MVP and proposed the first versioned season and backfill REST API contract.
- 2026-07-28 — Implemented and verified cache-backed FastF1 schedule discovery, latest-snapshot calendar persistence, and idempotent season job planning.
- 2026-07-28 — Implemented and verified the single-concurrency archive worker with scheduled heartbeat, recovery, fenced outcomes, and parent reconciliation.
- 2026-07-28 — Implemented and verified transactional parent-job aggregation with monotonic state and progress counts.
- 2026-07-28 — Implemented and verified pure current-season coverage and archive correction-checkpoint eligibility decisions.
- 2026-07-28 — Implemented and verified bounded stale-lease recovery with normal retry policy and resumed-worker fencing.
- 2026-07-28 — Implemented and verified ownership-fenced heartbeat writes and claim-aware atomic archive completion.
- 2026-07-28 — Implemented and verified transactional job-session claiming and synchronized retry/terminal failure transitions with job/session ownership tokens.
- 2026-07-28 — Implemented and verified typed runtime settings, original-exception retry classification, and deterministic equal-jitter backoff calculations.
- 2026-07-28 — Accepted retry, backoff, heartbeat, lease recovery, stale-worker fencing, and current-season freshness policies.
- 2026-07-28 — Recreated the ignored backend virtual environment with native macOS Python 3.13 and verified VS Code dependency imports against the locked environment.
- 2026-07-28 — Implemented and verified managed archive attempt states, attempt counting, overlap/source protection, and secret-free failure recording.
- 2026-07-28 — Implemented and verified the database-bound one-session FastF1 loading, normalization, and transactional persistence vertical slice.
- 2026-07-28 — Implemented and verified the serialized cache-backed FastF1 one-session loader with messages enabled and telemetry/weather disabled.
- 2026-07-28 — Implemented and verified atomic persistence and stale-row replacement for one normalized FastF1 archive session.
- 2026-07-28 — Accepted the one-session archive replacement contract, locked FastF1 3.8.3, and implemented and verified the pure normalization layer.
- 2026-07-28 — Implemented and verified Alembic Revision 2 with drivers, session entries, normalized results, and lap summaries.
- 2026-07-28 — Accepted all recommended Revision 2 decisions and finalized the sporting-data design for implementation.
- 2026-07-28 — Inspected FastF1 3.8.3 sporting data, accepted the 2018 ingestion boundary and session-scoped racing-number identity, and proposed Revision 2.
- 2026-07-27 — Accepted the database proposal and implemented and verified Alembic Revision 1 with six backfill control-plane tables.
- 2026-07-27 — Added the proposed Alembic and database model design with phased migrations and explicit open decisions.
- 2026-07-27 — Corrected repository documentation to English and recorded the implemented local scaffold.
- 2026-07-27 — Created persistent project memory and recorded the initial architecture, backfill requirements, and live timing goals.
