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

The local-development scaffold, five database migrations, locked FastF1 runtime, schedule discovery and season job planner, a managed database-bound one-session FastF1 archive worker, the historical season and session API slices, and the season plus session-exploration dashboard milestones are implemented. The API provides `POST /api/v1/seasons/{season_year}/backfill`, `GET /api/v1/seasons/{season_year}`, `GET /api/v1/backfill-jobs/{job_id}`, `GET /api/v1/upstreams/fastf1/usage`, `GET /api/v1/sessions/{session_id}`, `GET /api/v1/sessions/{session_id}/results`, and `GET /api/v1/sessions/{session_id}/entries/{session_entry_id}/laps` with strict response/error contracts, JavaScript-safe decimal-string database identifiers, UTC timestamp normalization, bounded query validation, and client-safe failures. Strict session-detail, entry/result, lap-summary, lap-filter, and page-cursor Pydantic models, their repeatable-read PostgreSQL query services, and thin no-store HTTP routes are implemented. The season overview reads one repeatable PostgreSQL snapshot and never writes or contacts FastF1. The job-progress endpoint reads one repeatable snapshot, derives internally consistent counts and an execution phase with current/next/last-completed session references, preserves deterministic round/session ordering, and never runs parent aggregation. The idempotent backfill command synchronously checks schedule coverage through the persistent FastF1 cache, delegates all planning and concurrency control to the season planner, creates or reuses one active job, and exposes its polling location without performing session ingestion in the API process. The managed ingestion flow adds observable pending/running/completed/failed session-ingestion state and fixed sanitized failure diagnostics around serialized cache-backed loading, pure sporting-data normalization, and atomic archive persistence. Validated runtime settings, retryable/terminal exception classification, deterministic equal-jitter backoff calculations, transactional job-session claiming, synchronized retry/terminal failure transitions, ownership-fenced heartbeat writes, claim-aware atomic completion, bounded stale-lease recovery, deterministic season/session freshness eligibility, transactional parent-job aggregation, and single-concurrency worker execution are implemented. Claims use a persistent PostgreSQL FastF1 request gate plus row locking and return job-attempt and monotonic session-attempt ownership tokens; heartbeat, failure, and completion writes validate both tokens. Archive session starts retain a one-second safety gap. Real cache-miss FastF1 HTTP sends are recorded in a shared rolling PostgreSQL ledger; the application warns at 400 and pauses at 450 observed requests per hour, below the library's 500-request threshold. FastF1's explicit rate-limit exception closes the global gate for one hour without consuming the job-session retry budget. Recovery fences the lost claim by leaving running state before a retry can be claimed. Freshness functions evaluate UTC coverage expiry, archive grace, and correction checkpoints. The season planner uses FastF1's curated schedule as the championship membership and round-number authority, retains exact private-index boundaries for matched events, hydrates missing historical or already-started events from exact per-session timing metadata, defers unpublished current-season future events without blocking available work, persists the available exact-boundary calendar snapshot atomically, and creates or reuses one active year job under a season advisory lock. Aggregation locks all child rows before the parent, preserves monotonic job state, and returns progress counts. The worker polls eligible jobs, maintains heartbeats during blocking FastF1 work, runs recovery/parent reconciliation every 30 seconds, applies fenced outcomes, and stops gracefully without taking new work. The React dashboard selects supported seasons, presents coverage and session-state visualizations, starts or reuses backfill jobs, polls active job progress, identifies the GP/session currently fetching, displays deferred future-event notices, and shows local request usage and cooldown countdowns through the backend only. Every calendar session can open an in-page workspace backed only by the historical session API; it shows metadata and availability, a complete entry/result table, participant selection, a compound-colored loaded-lap pace profile, detailed lap summaries, and 50-row keyset pagination with snapshot-change restart protection. Vitest and React Testing Library protect session loading, unavailable/error states, participant lap traversal, and snapshot-change behavior; Playwright protects primary season, synchronization, session, pagination, and responsive workflows in desktop and mobile Chromium. The database contains the backfill control plane, request coordination and accounting, schedule membership markers, and normalized sporting-data tables. Manual lap selection/averaging, telemetry, and live timing ingestion are not yet implemented.

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
  a PostgreSQL gate with a one-second safety gap and a one-hour explicit
  rate-limit cooldown.
- Real cache-miss archive and schedule HTTP sends share a rolling PostgreSQL
  request ledger, 400-request warning threshold, and 450-request application
  pause below the FastF1 library's 500-request threshold.
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
│   │   │   ├── seasons.py
│   │   │   ├── session_data.py
│   │   │   ├── sessions.py
│   │   │   └── upstream_usage.py
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
│   │   │   ├── request_budget.py
│   │   │   ├── request_budget_errors.py
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
│   │   ├── test_historical_session_contracts.py
│   │   ├── test_runtime_policy.py
│   │   ├── test_request_budget.py
│   │   ├── test_season_endpoint.py
│   │   ├── test_season_backfill.py
│   │   ├── test_season_backfill_endpoint.py
│   │   ├── test_season_backfill_endpoint_integration.py
│   │   ├── test_season_overview.py
│   │   ├── test_season_status.py
│   │   ├── test_session_data.py
│   │   ├── test_session_endpoints.py
│   │   ├── test_sporting_data_integration.py
│   │   ├── test_upstream_usage_endpoint.py
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
│   ├── HISTORICAL_SESSION_API_DESIGN.md
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
- `backend/app/api/`: Versioned historical API, strict response/error models, supported-year validation, season/job/request-budget/session read models and routes, and the idempotent backfill command boundary.
- `backend/app/db/`: SQLAlchemy metadata, connection configuration, session factory, and Revision 1–5 models.
- `backend/app/ingestion/`: Managed attempt state, schedule discovery and season planning, transactional backfill claiming/failure/aggregation transitions, single-concurrency worker execution, database-bound one-session orchestration, cache-backed loading, request-level accounting, pure upstream-to-domain normalization, atomic archive persistence, and runtime/freshness policy primitives.
- `backend/alembic/`: Alembic environment and reviewed migration revisions.
- `backend/tests/`: Backend tests.
- `frontend/src/`: React dashboard source.
- `docs/`: Architecture, decisions, and persistent project context.
- `docs/BACKFILL_RUNTIME_POLICY.md`: Accepted retry, backoff, heartbeat, lease recovery, fencing, current-season freshness, parent aggregation, and worker execution policy.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic conventions, migration phases, tables, constraints, indexes, and recovery behavior.
- `docs/FASTF1_INGESTION_CONTRACT.md`: Accepted one-session validation, identity, atomic replacement, failure, and idempotency contract.
- `docs/HISTORICAL_API_DESIGN.md`: Accepted and implemented first historical season and backfill REST API contract.
- `docs/HISTORICAL_SESSION_API_DESIGN.md`: Accepted session-detail,
  entry/result, paginated lap-summary, and future manual post-session analysis
  contract; response/query models, database read services, and HTTP routes are
  implemented.
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

### Explanatory Git commit messages

- Decision: Every Git commit must use a concise subject plus an explanatory
  body describing the change scope, rationale, and relevant verification.
- Rationale: Preserve useful project history so future reviews can understand
  not only what changed, but why it changed and how it was checked.
- Date: 2026-07-28
- Status: implemented

### First-five-milestone feature branch

- Decision: Implement and commit the first five approved roadmap milestones on
  `feature/historical-analysis-telemetry`, keeping `main` fixed at `70176ea`
  until the user explicitly requests a merge or other movement.
- Rationale: Isolate the multi-milestone work for review and preserve the
  current stable mainline exactly as requested.
- Date: 2026-07-28
- Status: implemented

### Frontend test stack

- Decision: Use Vitest, React Testing Library, `user-event`, `jest-dom`, and
  jsdom for deterministic frontend tests, plus Playwright Chromium with
  intercepted backend responses for desktop and mobile browser workflows.
- Rationale: Cover component state and real interaction/responsive behavior
  without depending on live FastF1 traffic or mutable local database contents.
- Date: 2026-07-28
- Status: implemented

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

### Historical session exploration UI

- Decision: Let every season-calendar session open one in-page workspace that
  reads session detail and, when available, the full entry/result set. Load
  laps only after a participant is selected, request at most 50 rows per page,
  append pages by `next_after_lap`, and restart pagination when the completed
  snapshot timestamp changes.
- Rationale: Make completed sessions useful without loading laps for an entire
  season, preserve session-entry identity, keep payloads bounded, and prevent
  one visible lap series from silently combining two archive snapshots.
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

### FastF1 historical identity and tyre-parser compatibility

- Decision: Treat FastF1 3.8.3's literal case-insensitive `nan` driver ID as
  missing and use the existing racing-number fallback. For the pinned library's
  malformed bunched-stint `IndexError`, retry only its instance-local
  tyre-correction helper after moving out-of-range first-timestamp messages to
  FastF1's existing one-day fallback boundary; propagate unrelated failures.
- Rationale: Historical substitute drivers otherwise collapse into the same
  false global identity and duplicate entry key. The bounded parser repair
  preserves full lap data for the affected 2018 Italian Grand Prix race without
  weakening database uniqueness or accepting a result-only snapshot.
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
- Status: superseded

### Historical personal-best nullability

- Decision: Preserve a missing historical FastF1 `IsPersonalBest` value as null instead of rejecting the entire session or inventing `false`.
- Rationale: Valid 2018 archive laps can lack a boolean personal-best flag; unknown and false are different facts.
- Date: 2026-07-28
- Status: implemented

### Curated schedule membership and current-season deferral

- Decision: Use the curated public FastF1 schedule as the championship membership and round-number authority. Match private-index events strictly by normalized name and retain their session boundaries. Require exact F1 timing `session_info` start, end, and offset metadata for a missing past-season event or a missing current-season event that has started. For the current UTC season only, defer a private-index-missing event whose earliest curated session start is still in the future; never estimate a duration or block already available sessions.
- Rationale: The cached 2026 private index assigned round 6 to both Miami and Monaco and currently stops at Hungary while the curated schedule contains 11 later future events. The cached 2018 private index omitted the Australian Grand Prix entirely. Curated UTC session starts safely distinguish expected future publication delay from a historical omission, while exact metadata remains mandatory before a session can become ingestible.
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

### Cache-aware FastF1 request budget

- Decision: Replace the conservative 90-second session-start interval with a one-second safety gap; record real cache-miss FastF1 HTTP sends in a shared rolling one-hour PostgreSQL ledger; warn at 400 observed sends and pause at 450 below the FastF1 library's 500-request threshold; preserve retry budgets during local budget pauses; and expose the estimate as explicitly non-authoritative.
- Rationale: A FastF1 session uses multiple HTTP resources while cache hits use none, so session-level delay neither represents true request use nor provides useful progress. Request-level accounting permits faster cached work while keeping cross-process headroom and an exact recovery timestamp.
- Date: 2026-07-28
- Status: implemented

### Observable backfill execution

- Decision: Derive current, next, and last-completed session references plus `ready`, `fetching`, `pacing`, `rate_limit_cooldown`, `request_budget_cooldown`, `retry_backoff`, `idle`, or `terminal` execution phases in the read-only job response. Display the phase, GP/session names, countdown, and grouped child sessions in the dashboard.
- Rationale: Users can understand whether work is actively fetching or intentionally waiting and can decide whether local request headroom is sufficient before starting more work.
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

### Historical session read API contract

- Decision: Expose separate read-only session-detail, entry/result, and
  session-entry-scoped lap-summary endpoints. Keep results unpaginated, paginate
  laps by lap-number keyset with bounded filters, include deleted laps by
  default, return `409` when a known session has no completed snapshot, and
  serve preserved completed snapshots during correction attempts.
- Rationale: Keep responses bounded and deterministic, preserve session-local
  driver identity, distinguish unavailable data from a valid empty result, and
  support web and iOS session exploration without embedding laps or telemetry
  in season responses.
- Date: 2026-07-28
- Status: accepted

### Historical session contract foundation

- Decision: Implement strict Pydantic models for session detail, snapshot
  availability, ingestion state, entries/results, exact decimal-string points,
  lap queries, filters, pages, and full lap summaries before database services
  or HTTP routes.
- Rationale: Validate cross-field availability, deterministic ordering,
  JavaScript-safe identity, lap-filter, and pagination guarantees independently
  from PostgreSQL and transport behavior.
- Date: 2026-07-28
- Status: implemented

### Historical session database reads

- Decision: Read session detail, entries/results, and one entry-scoped lap page
  inside independent PostgreSQL `REPEATABLE READ, READ ONLY` transactions. Use
  completed ingestion timestamps for availability, return zero archive counts
  before completion, preserve usable snapshots during correction failures, and
  paginate laps by the existing `(session_entry_id, lap_number)` key.
- Rationale: Give web and future iOS clients internally consistent, mutation-free
  sporting snapshots while keeping provisional data out of historical reads and
  avoiding offset drift or a new database index.
- Date: 2026-07-28
- Status: implemented

### Historical session HTTP boundary

- Decision: Expose the three accepted session read services through thin
  `/api/v1/sessions` GET routes with positive path identifiers, bounded lap
  query parameters, a stable error for inverted lap ranges, no-store headers,
  strict response models, and OpenAPI documentation for both standard FastAPI
  validation and stable API errors.
- Rationale: Keep transport validation and failure mapping explicit while
  preserving the repeatable-read database services as the read-model boundary
  for web and future iOS clients.
- Date: 2026-07-28
- Status: implemented

### Manual post-session analysis readiness

- Decision: Preserve and expose individual lap-summary timing, stint, tyre, pit,
  track-status, deletion, and accuracy fields so clients can manually select
  representative laps and compare calculated pace. Associate a manual selection
  with session ID, session-entry ID, lap numbers, and the completed snapshot
  timestamp. Defer automatic race-simulation classification, saved analyses,
  and server-side aggregates to a separately designed future feature.
- Rationale: Manual selection can support transparent practice long-run
  comparisons with the current relational data, while verified fuel load,
  engine mode, run plan, and weather are unavailable and make automatic
  classification inherently inferential.
- Date: 2026-07-28
- Status: accepted

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

Alembic revision `20260728_0005` implements local request accounting:

- Adds `upstream_request_events` with a `BIGINT IDENTITY` primary key,
  constrained `fastf1` source, constrained `archive` or `schedule` operation,
  UTC request timestamp, and timestamp index.
- Extends the request-gate reason constraint with `budget`.
- Keeps archive and schedule processes under one transactionally reserved
  rolling-hour operational ceiling.

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
- Returns ordered `deferred_future_events` metadata when current-season future
  events are known publicly but do not yet have exact FastF1 timing boundaries.
- Delegates schedule refresh and idempotent job planning to `ensure_season_backfill`; it does not ingest a FastF1 session in the request.
- Returns stable, sanitized `409`, `429`, `500`, `502`, and `503` failures for
  planning conflicts, a local request-budget pause, configuration, invalid
  snapshots, upstream availability, and database availability. A `429` includes
  the exact `Retry-After` delay.

### `GET /api/v1/backfill-jobs/{job_id}`

- Returns `200 OK` with parent lifecycle details, derived progress counts, a
  derived execution phase/current/next/last-completed snapshot, and every child
  session in deterministic round/session order.
- Reads PostgreSQL only and never runs parent aggregation or writes data.
- Returns `Cache-Control: no-store`.
- Returns FastAPI's standard `422` validation response for malformed UUIDs.
- Returns stable `404 backfill_job_not_found` for unknown UUIDs.
- Returns stable, sanitized `500 server_configuration_error` and `503 database_unavailable` responses.

### `GET /api/v1/upstreams/fastf1/usage`

- Returns `200 OK` with the local rolling-window total, archive/schedule split,
  thresholds, capacity, cooldown, and status.
- Returns `Cache-Control: no-store` and `authoritative: false`.
- Returns stable, sanitized `500 server_configuration_error` and
  `503 database_unavailable` responses.

### `GET /api/v1/sessions/{session_id}`

- Returns `200 OK` for an existing session, including event/session metadata,
  current ingestion state, completed-snapshot availability, and bounded
  sporting row counts.
- Returns an existing session with unavailable snapshot metadata and zero
  counts rather than treating it as a missing resource.
- Returns `Cache-Control: no-store`.
- Returns FastAPI's standard `422` validation response for malformed or
  non-positive session IDs.
- Returns stable `404 session_not_found`, sanitized
  `500 server_configuration_error`, and `503 database_unavailable` responses.

### `GET /api/v1/sessions/{session_id}/results`

- Returns `200 OK` with every stored session entry and its nullable result,
  ordered deterministically by result position and session-entry ID.
- Preserves exact points as decimal strings and uses the session entry, never
  racing number, as participant identity.
- Returns `Cache-Control: no-store`.
- Returns FastAPI's standard `422` validation response for malformed or
  non-positive session IDs.
- Returns stable `404 session_not_found`, `409 session_data_unavailable`,
  sanitized `500 server_configuration_error`, and
  `503 database_unavailable` responses.

### `GET /api/v1/sessions/{session_id}/entries/{session_entry_id}/laps`

- Returns `200 OK` with one lap-number-keyset page for an entry belonging to
  the requested session.
- Accepts `after_lap`, `limit`, `lap_from`, `lap_to`, `stint_number`, and
  `include_deleted`; the default limit is 50 and the maximum is 100.
- Includes deleted laps by default and supports deterministic empty pages.
- Returns `Cache-Control: no-store`.
- Returns FastAPI's standard `422` validation response for malformed or
  out-of-bound path/query values and stable `422 invalid_lap_range` when
  `lap_from` exceeds `lap_to`.
- Returns stable `404 session_not_found`, `404 session_entry_not_found`,
  `409 session_data_unavailable`, sanitized
  `500 server_configuration_error`, and `503 database_unavailable` responses.

No telemetry or WebSocket endpoint has been implemented. The versioned
`/api/v1` router, strict historical response/error models, supported-year
validation, pure derived season-status policy, read-only season, job-progress,
request-usage, session-detail, result, and lap-summary endpoints, and
idempotent backfill command are implemented.

The accepted first historical API contract is documented in
`docs/HISTORICAL_API_DESIGN.md`.

The historical session-detail, entry/result, and paginated lap-summary contract
is accepted in `docs/HISTORICAL_SESSION_API_DESIGN.md`. Its strict Pydantic
response/query models, repeatable-read PostgreSQL services, thin HTTP routes,
stable failure mappings, and OpenAPI paths are implemented and designed to
support future manual post-session lap selection and pace comparison. No
analysis calculation or analysis UI from that contract has been implemented.

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
before session state and reserves a one-second session-start safety gap. Inside
the serialized FastF1 boundary, the pinned cache session reserves the rolling
PostgreSQL budget only when it reaches a real raw HTTP send after a cache miss.
At 450 observed sends in one hour, a local budget pause returns the session to
pending, restores its job-session retry budget, retains the monotonic lifetime
ingestion token, and uses the exact oldest-request-plus-window retry timestamp.
An explicit FastF1 rate-limit failure remains distinct and closes the shared gate
for one hour. Schedule discovery retains raw session boundaries for matched
events, uses the curated schedule for complete championship membership, and
hydrates missing historical or already-started events only through exact
per-session timing metadata. Unpublished current-season future events are
deferred until a later six-hour coverage refresh and do not block planning for
available sessions.
Historical laps with an unknown `IsPersonalBest` value are stored as null.

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
- Added Alembic Revision 5 and SQLAlchemy metadata for a shared rolling FastF1
  request-event ledger and `budget` gate reason.
- Instrumented the pinned FastF1 cache-miss send path so archive and schedule
  operations reserve only real outbound requests; implemented a 400-request
  warning, 450-request operational pause, exact rolling recovery timestamp, and
  retry-budget preservation.
- Replaced 90-second session-start pacing with a one-second safety gap while
  retaining single-session worker concurrency and explicit one-hour FastF1
  rate-limit handling.
- Added `GET /api/v1/upstreams/fastf1/usage` and extended job progress with
  execution phase, next action, current session, next session, and last
  completed session.
- Added the dashboard request-budget panel, one-second cooldown countdown,
  current/next/last GP-session cards, and event-grouped job-session details.
- Verified the live 2018 dashboard shows all 21 rounds beginning with Australian
  Grand Prix, the local request estimate, and an active job's pacing/current/next
  execution details.
- Verified all 314 backend tests against isolated PostgreSQL 17, Ruff, the
  frontend production build, Compose configuration, Revision 5
  upgrade/downgrade/re-upgrade, and zero Alembic model/schema drift.
- Committed the reviewed cache-aware request-budget and detailed-progress phase
  locally as `ef89f35`; no push was performed.
- Diagnosed the seven remaining 2018 ingestion failures from the persistent
  control plane and cached FastF1 payloads: six Practice 1 sessions used the
  literal `nan` driver-ID sentinel, while the Italian Grand Prix race hit a
  pinned FastF1 3.8.3 malformed tyre-stint `IndexError`.
- Implemented literal `nan` identity handling and an instance-local, bounded
  FastF1 tyre-parser compatibility retry with focused regression coverage.
- Replayed the seven real cached payloads successfully, created a normal
  seven-session retry job, and verified it completed with no failed children.
- Atomically repaired seven earlier completed session entries that still
  referenced the false global `nan` driver, then removed the unreferenced
  synthetic driver row without changing their results or laps.
- Verified the live local 2018 API reports `completed` with 21 events and
  105/105 completed sessions, no active job, 2,100 entries, 2,100 results,
  58,002 laps, and no remaining `nan` driver or entry identity.
- Verified Ruff and all 316 backend tests against a dedicated isolated
  PostgreSQL 17 database; removed the temporary test database afterward.
- Refined the existing dashboard without adding product behavior or package
  dependencies: introduced a more polished race-archive visual system, stronger
  season hierarchy, elevated control and status surfaces, clearer metric and
  progress presentation, richer event/session cards, and responsive mobile
  treatments while preserving the existing API contracts and interactions.
- Verified the refined frontend with the TypeScript and Vite production build
  and a clean repository whitespace check.
- Implemented current-season future-event deferral using curated UTC session
  starts while retaining strict exact-boundary hydration for past or
  already-started missing events.
- Propagated ordered deferred-event metadata through the season planner and
  backfill command response; the dashboard now explains how many future events
  are awaiting exact timing and identifies the first one.
- Verified the cached real 2026 schedule as 11 available events and 55 exact
  sessions through the Hungarian Grand Prix plus 11 deferred future events
  beginning with the Dutch Grand Prix, without requesting their unavailable
  session metadata or changing the live 2026 database state.
- Verified Ruff, all 319 backend tests against an isolated PostgreSQL 17
  database, and the frontend TypeScript/Vite production build.
- Created the proposed historical session-detail, entry/result, and
  entry-scoped paginated lap-summary REST contract without implementing routes,
  queries, migrations, or UI behavior.
- Accepted the complete historical session-read contract and documented how its
  individual lap fields and snapshot identity support future manual
  post-session pace analysis without claiming automatic race-run detection.
- Implemented strict historical session-detail, snapshot, ingestion,
  entry/result, lap-query/filter/page, and full lap-summary Pydantic contracts.
- Added focused contract tests for exact decimals, canonical identifiers and
  colors, UTC timestamps, unavailable snapshots, nullable identities,
  deterministic ordering, lap filters, and cursor/page consistency.
- Verified Ruff and all 337 backend tests against a fresh isolated PostgreSQL
  17 database, then removed the temporary test database.
- Implemented read-only session-detail, entry/result, and entry-scoped
  paginated lap-summary PostgreSQL services with dedicated not-found and
  unavailable-data failures.
- Added integration coverage for preserved failed snapshots, zero unavailable
  counts, result ordering and nullable identities, team-color normalization,
  keyset traversal, lap filters, empty pages, entry ownership, identifier
  validation, and read-only transactions.
- Verified Ruff and all 352 backend tests against a fresh isolated PostgreSQL
  17 database, then removed the temporary test database.
- Implemented the three accepted read-only historical session HTTP routes with
  positive path validation, bounded lap filters and keyset parameters, stable
  domain/database errors, and `Cache-Control: no-store`.
- Documented strict session-detail, result, and lap response models plus the
  standard and stable validation/error forms in generated OpenAPI.
- Added endpoint coverage for response serialization, every query parameter,
  path/query validation, inverted ranges, domain and database failure hygiene,
  no-store headers, dependency wiring, and OpenAPI schemas.
- Verified Ruff and all 379 backend tests against a fresh isolated PostgreSQL
  17 database, then removed the temporary test database.
- Smoke-tested all three routes against an existing completed session through
  the running local Compose API; each returned `200`, and session detail
  returned `Cache-Control: no-store`.
- Extended the frontend's TypeScript contracts and same-origin API client for
  session detail, results, and bounded lap-summary requests.
- Made every season-calendar session an accessible drilldown control and added
  an in-page session workspace with metadata, snapshot availability, sporting
  row counts, entry/result classification, and participant selection.
- Added a compound-colored loaded-lap pace profile, detailed timing/stint/tyre/
  sector/quality table, 50-row keyset pagination, empty/error/loading states,
  and archive-snapshot change protection without adding dependencies.
- Verified the frontend TypeScript/Vite production build, the running local
  Vite module, and live local session-detail, result, and lap API responses.
- Created the approved five-milestone execution plan and isolated the work on
  `feature/historical-analysis-telemetry` while preserving `main` at `70176ea`.
- Added the frontend Vitest/React Testing Library and Playwright foundations,
  deterministic historical API fixtures, four session-workspace component
  tests, and six desktop/mobile Chromium workflow tests.
- Verified all four frontend component tests, all six browser tests, and the
  TypeScript/Vite production build.

No manual lap-selection/average analysis, telemetry feature, or live timing
feature has been completed.

## Work in Progress

- Manual selected-lap pace analysis is the active milestone. It will remain
  ephemeral and snapshot-bound, support two participant/team selections, and
  will not claim automatic race-run classification.

## Next Steps

1. Implement the accepted ephemeral manual lap-selection analysis workflow,
   including selected-lap averages, two participant/team comparison, quality
   visibility, and snapshot-change handling.
2. Measure representative FastF1 telemetry volume and access patterns before
   deciding whether PostgreSQL alone or TimescaleDB should own telemetry.
3. Design and implement bounded telemetry ingestion and APIs queried by session,
   driver, and lap; never include season-wide telemetry in overview responses.
4. Add automatic current-season planning so newly published event/session
   boundaries and correction checkpoints do not depend indefinitely on a manual
   dashboard command. Revisit persistent deferred-event metadata at this point.
5. Design the SignalR live-timing protocol boundary, reconnect/resume,
   deduplication, provisional schema, and FastF1 finalization/reconciliation
   rules before implementing live ingestion.
6. Implement the live collector, provisional persistence, backend WebSocket
   fan-out, session finalization, and dashboard live views.
7. Stabilize the shared API for the SwiftUI client, then implement the iOS
   application without exposing upstream credentials.
8. Before production, add authentication/authorization, secret management,
   secure PostgreSQL configuration, observability, backups, CI, deployment,
   and any demonstrated background-job infrastructure. Reconsider manual job
   cancellation and Redis only when measurements justify them.

## Run and Test Commands

Verified:

```bash
npm run build --prefix frontend
npm test --prefix frontend
npm run test:e2e --prefix frontend
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

Database integration tests additionally require `TEST_DATABASE_URL` and a
migrated PostgreSQL database. The complete suite passed with 379 tests against
a fresh isolated PostgreSQL 17 database after the historical session HTTP
routes and endpoint tests were added.

## Known Issues and Technical Debt

- Loader tests use controlled FastF1 doubles and do not perform an upstream network smoke test.
- Schedule discovery combines FastF1 3.8.3's curated public schedule with its private, cache-decorated F1 timing APIs because neither source alone supplies both complete membership and exact end timestamps. The exact FastF1 pin and focused contract tests contain that compatibility risk.
- Deferred current-season future-event metadata is returned only by a command
  that actually performs schedule refresh; it is not persisted, so a fresh
  repeat command does not reconstruct the same notice without another refresh.
- Request accounting instruments FastF1 3.8.3's private
  `_SessionWithRateLimiting.send` path because that is the pinned cache-miss
  boundary. Focused compatibility tests contain this risk, but a FastF1 upgrade
  must revalidate the hook.
- The Italian 2018 race compatibility path wraps FastF1 3.8.3's private
  `_Session__fix_tyre_info` method on one session instance. It retries only the
  observed malformed bunched-stint bracket condition, but a FastF1 upgrade must
  revalidate or remove this compatibility layer.
- The request ledger is a local estimate beginning with Revision 5. It cannot
  observe prior traffic or traffic from other processes/machines that do not use
  this database, and FastF1 provides no supported remaining-quota query.
- Failure recording requires a separate database transaction; if the database is unavailable, the original exception is re-raised with a diagnostic note but failed state cannot be persisted.
- Session-row locking serializes callers that use persistence, recovery, aggregation, and worker services.
- FastF1 loading and cache activation remain serialized only within each process. Archive session starts are now cross-process paced through PostgreSQL, but API schedule discovery and cache file access do not yet share a cross-process mutex.
- Earlier terminal 2018 jobs remain immutable failure history. A later targeted
  retry job completed all seven repaired sessions, and the current local season
  snapshot contains 105 completed and zero failed sessions.
- TimescaleDB usage has not been decided.
- Recovery deliberately skips inconsistent rows whose persistent session is missing, owned by another source, completed, non-running, or has a fresh heartbeat. Such rows can remain running at job-session level until a future reconciliation policy is implemented.
- The worker does not invoke season planning; the implemented POST backfill command must run before newly eligible rows can be processed.
- Graceful shutdown waits for active in-process FastF1 work; local Compose allows two minutes before forced termination, after which lease recovery applies.
- Manual job cancellation is intentionally deferred from the historical MVP.
- Historical session response/query contracts, PostgreSQL read services, HTTP
  routes, OpenAPI paths, and the base dashboard result/lap workspace are
  implemented. Manual post-session selection and analysis remain a future
  workflow; no saved-analysis model or automatic race-run classifier has been
  designed.
- The dashboard has build and live local smoke coverage but no dedicated
  coverage threshold yet. Dedicated component and desktop/mobile browser
  interaction suites are implemented, but CI execution remains future work.
- Docker registry metadata timed out during the latest image rebuild attempt; the existing images and bind-mounted source started successfully, and the local dashboard/API health checks passed.
- FastF1 ingestion time and storage volume have not been measured.
- Live SignalR protocol and reconciliation rules have not been designed.
- PostgreSQL trust authentication is suitable only for the current loopback-bound local environment.
- Virtual environments are platform-specific. Never reuse a `.venv` created inside the Linux container on macOS; recreate the ignored host environment from `uv.lock`.

## Important Files

- `AGENTS.md`: Mandatory context, safety, language, and user-change preservation rules.
- `docs/FIRST_FIVE_MILESTONES_PLAN.md`: Approved detailed deliverables,
  acceptance criteria, verification, and commit boundaries for the active
  five-milestone feature branch.
- `docs/PROJECT_CONTEXT.md`: Authoritative record of current behavior, accepted decisions, and next steps.
- `docs/BACKFILL_RUNTIME_POLICY.md`: Accepted runtime retry, backoff,
  heartbeat, lease recovery, fencing, freshness, and request-budget decisions.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic layout, relational model, migration phases, idempotency, locking, and recovery design.
- `docs/FASTF1_INGESTION_CONTRACT.md`: Accepted one-session archive snapshot identity, validation, atomic replacement, and failure behavior.
- `docs/HISTORICAL_API_DESIGN.md`: Accepted and implemented first historical season/backfill API contract.
- `docs/HISTORICAL_SESSION_API_DESIGN.md`: Accepted session-detail,
  entry/result, paginated lap-summary, and future manual post-session analysis
  contract; response/query models, database services, and HTTP routes are
  implemented together with the base dashboard exploration behavior.
- `backend/app/api/backfill_job.py`: Repeatable-read, database-read-only job and child-session mapping with derived counts, deterministic ordering, and dedicated not-found behavior.
- `backend/app/api/backfill_jobs.py`: Read-only job-progress HTTP route, UUID validation, response contract, no-store policy, and sanitized failure mappings.
- `backend/app/api/contracts.py`: Strict historical API response/error models,
  session/result/lap contracts, lap-query pagination and filters, enum values,
  UTC timestamps, exact decimal strings, decimal-string identifiers, and
  cross-field validation.
- `backend/app/api/dependencies.py`: Supported 2018-through-current-UTC-year validation, database and schedule-loader injection, and stable API dependency errors.
- `backend/app/api/errors.py`: Stable client-safe FastAPI error envelope.
- `backend/app/api/router.py`: Mounted `/api/v1` router boundary for historical product endpoints.
- `backend/app/api/season_overview.py`: Repeatable-read, database-read-only latest-membership season overview, eligibility/count mapping, active-job summary, and derived status.
- `backend/app/api/seasons.py`: Season overview and backfill-command routes, dynamic command response mapping, polling headers, and sanitized failure mappings.
- `backend/app/api/season_status.py`: Pure validated derived season-status precedence.
- `backend/app/api/session_data.py`: Repeatable-read, database-read-only session
  detail, result, and entry-scoped keyset lap services with snapshot
  availability and dedicated domain failures.
- `backend/app/api/sessions.py`: Thin read-only session-detail, result, and
  entry-scoped lap HTTP routes with bounded validation, no-store headers, stable
  sanitized failure mappings, and strict OpenAPI contracts.
- `docs/SCHEDULE_DISCOVERY_DESIGN.md`: Implemented FastF1 schedule source, normalized snapshot, atomic membership persistence, and season job-planning contract.
- `docs/SPORTING_DATA_DESIGN.md`: Implemented Revision 2 schema, FastF1 inspection evidence, normalization rules, and decisions.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `backend/alembic/versions/20260727_0001_backfill_control_plane.py`: Reviewed Revision 1 schema and downgrade.
- `backend/alembic/versions/20260728_0002_sporting_data.py`: Reviewed Revision 2 sporting-data schema and downgrade.
- `backend/alembic/versions/20260728_0003_schedule_discovery.py`: Revision 3 schedule membership markers, coverage invalidation, indexes, and downgrade.
- `backend/alembic/versions/20260728_0004_ingestion_resilience.py`: Revision 4 FastF1 request-gate, historical personal-best nullability, seed data, and downgrade.
- `backend/alembic/versions/20260728_0005_request_budget.py`: Revision 5 shared
  FastF1 request-event ledger, budget gate reason, and downgrade.
- `backend/app/db/base.py`: Shared SQLAlchemy metadata and timestamp mixin.
- `backend/app/db/models/`: Revision 1 control-plane and Revision 2 sporting-data SQLAlchemy models.
- `backend/app/db/models/request_gate.py`: Persistent cross-worker FastF1
  pacing, request-budget, and rate-limit cooldown state.
- `backend/app/db/models/request_event.py`: Observed FastF1 archive/schedule
  cache-miss send ledger.
- `backend/app/ingestion/archive_attempt.py`: Pending/running/failed archive attempt transitions, attempt counting, overlap protection, and fixed sanitized failure mappings.
- `backend/app/ingestion/backfill_worker.py`: Single-concurrency claim/execution loop, heartbeat monitor, recovery/aggregation maintenance, fixed outcome handling, and graceful shutdown.
- `backend/app/ingestion/backfill_orchestration.py`: Transactional job-session claiming, synchronized persistent state, fenced heartbeat updates, retry/terminal failure transitions, stale-lease recovery, parent-job aggregation, and ownership-token validation.
- `backend/app/ingestion/archive_ingestion.py`: Database-target lookup, FastF1 request derivation, loaded-identity validation, normalization, and persistence composition.
- `backend/app/ingestion/fastf1_loader.py`: Deterministic, cache-backed,
  process-serialized one-session FastF1 loading with cache-miss request
  instrumentation and bounded pinned-library tyre-parser compatibility.
- `backend/app/ingestion/fastf1_schedule.py`: Curated championship membership,
  private-index reconciliation, exact missing-event timing hydration,
  current-season future-event deferral, and pure strict normalization.
- `backend/app/ingestion/request_budget.py`: Transactional rolling-window
  request reservation, exact capacity recovery, cooldown, and usage snapshots.
- `backend/app/api/upstream_usage.py`: Read-only local FastF1 request-usage
  endpoint and stable failures.
- `backend/app/ingestion/season_backfill.py`: Atomic latest-snapshot persistence and advisory-locked active-job creation/reuse.
- `backend/app/ingestion/fastf1_normalization.py`: Pure FastF1 results-and-laps normalization, literal missing-identity sentinel handling, and validation.
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
- `backend/tests/test_session_data.py`: PostgreSQL session metadata, preserved
  snapshot, result ordering, lap pagination/filtering, ownership, unavailable
  data, validation, and read-only transaction coverage.
- `backend/tests/test_session_endpoints.py`: Session-detail, result, and lap HTTP
  serialization, validation, stable-error, no-store, dependency, and OpenAPI
  coverage.
- `backend/tests/test_backfill_orchestration.py`: PostgreSQL claim locking, heartbeat synchronization, retry, lease recovery, parent aggregation, row-lock serialization, source protection, rollback, and ownership-token coverage.
- `backend/tests/test_archive_persistence.py`: PostgreSQL transactional persistence, idempotency, stale replacement, source protection, and rollback coverage.
- `backend/tests/test_archive_ingestion.py`: PostgreSQL one-session vertical-slice identity, idempotency, and pre-persistence failure coverage.
- `backend/tests/test_database_integration.py`: PostgreSQL constraint and index integration coverage.
- `backend/tests/test_sporting_data_integration.py`: Revision 2 identity, constraint, number-reuse, nullable-field, and idempotency integration coverage.
- `backend/tests/test_fastf1_loader.py`: FastF1 loader cache, configuration, flags, errors, and serialization tests.
- `backend/tests/test_fastf1_normalization.py`: FastF1 normalization happy-path and rejection coverage.
- `backend/tests/test_fastf1_schedule.py`: Schedule source, cache,
  serialization, curated membership, strict missing-event hydration,
  current-season future deferral, UTC boundary, canonical-key, and rejection
  tests.
- `backend/tests/test_request_budget.py`: PostgreSQL request reservation,
  threshold, cooldown, operation split, and warning coverage.
- `backend/tests/test_upstream_usage_endpoint.py`: Request-usage HTTP response,
  no-store, safe failure, and OpenAPI coverage.
- `backend/tests/test_season_backfill.py`: PostgreSQL calendar persistence, freshness, job reuse, concurrency, cancellation preservation, and rollback tests.
- `backend/tests/test_freshness_policy.py`: Coverage TTL, UTC year, exact grace/checkpoint, late-scan, stability, and timestamp validation coverage.
- `backend/tests/test_runtime_policy.py`: Runtime setting, environment parsing, retry classification, and backoff boundary coverage.
- `backend/tests/test_worker.py`: Worker startup maintenance, shutdown behavior, heartbeat interval validation, and secret-safe logging coverage.
- `backend/app/main.py`: FastAPI scaffold and health endpoints.
- `backend/app/worker.py`: Archive worker process setup, database/configuration readiness, signal handling, and readiness-file lifecycle.
- `backend/tests/test_health.py`: Backend health endpoint unit tests.
- `backend/tests/test_historical_session_contracts.py`: Session-detail,
  result, lap-query, lap-page, serialization, ordering, availability, and
  analysis-field contract coverage.
- `frontend/src/App.tsx`: Season selection, coverage metrics, request-budget
  visualization, detailed execution/countdown progress, event-grouped session
  states, backfill command, active-job polling, and session-workspace
  navigation.
- `frontend/src/SessionExplorer.tsx`: Session metadata and availability,
  entry/result classification, participant selection, compound-colored
  loaded-lap pace profile, detailed lap table, and snapshot-safe keyset
  pagination.
- `frontend/src/api.ts`: Typed same-origin season, backfill, request-budget,
  session-detail, result, and lap API client with stable safe error handling.
- `frontend/src/contracts.ts`: TypeScript representation of the implemented
  historical season, job, session, result, and lap API contracts.
- `frontend/src/index.css`: Responsive editorial motorsport layout, surface,
  typography, interaction, progress, event-card, result-table, lap-chart, and
  visual-state system.
- `frontend/src/SessionExplorer.test.tsx`: Component coverage for historical
  session availability, errors, participant laps, pagination, and snapshot
  replacement.
- `frontend/e2e/dashboard.spec.ts`: Intercepted desktop/mobile Chromium
  workflows for seasons, synchronization, session exploration, pagination,
  and viewport containment.
- `frontend/playwright.config.ts`: Deterministic desktop/mobile browser project
  and local Vite server configuration.
- `README.md`: Local development overview and commands.

## Change Log

- 2026-07-28 — Created the protected five-milestone feature branch and detailed
  execution plan; implemented and verified the Vitest/React Testing Library
  component suite and Playwright desktop/mobile browser workflow foundation.
- 2026-07-28 — Reviewed and approved the complete historical session API and
  base dashboard exploration milestone for a local commit; adopted explanatory
  commit bodies as a permanent repository rule.
- 2026-07-28 — Implemented and verified the base session-exploration dashboard
  with season-session navigation, classification, participant lap drilldown,
  compound pace visualization, detailed lap summaries, and snapshot-safe
  pagination.
- 2026-07-28 — Implemented and verified the three read-only historical session
  HTTP routes with bounded validation, no-store headers, stable errors, and
  OpenAPI coverage; all 379 backend tests passed against a fresh isolated
  database, and the running local API served all three routes successfully.
- 2026-07-28 — Implemented and verified the read-only historical session
  detail, entry/result, and entry-scoped paginated lap PostgreSQL services; all
  352 backend tests passed against a fresh isolated database.
- 2026-07-28 — Implemented and verified the strict historical session,
  entry/result, and paginated lap-summary response/query contracts; all 337
  backend tests passed against a fresh isolated PostgreSQL database.
- 2026-07-28 — Accepted the historical session-read API decisions and recorded
  the future manual selected-lap pace-analysis workflow, snapshot identity, and
  current inference limitations without implementing analysis behavior.
- 2026-07-28 — Proposed the bounded historical session-detail, entry/result,
  and entry-scoped paginated lap-summary REST contract for review; no endpoint
  or query implementation was added.
- 2026-07-28 — Reviewed and approved the historical FastF1 compatibility
  repair, dashboard visual refinement, and current-season future-event
  deferral for one local commit; recorded the next implementation milestones.
- 2026-07-28 — Implemented and verified current-season future-event deferral so
  available 2026 sessions can be planned while unpublished future timing is
  reported without blocking the command.
- 2026-07-28 — Refined the existing dashboard's visual hierarchy and responsive
  race-archive presentation without adding features or dependencies; verified
  the TypeScript/Vite production build.
- 2026-07-28 — Diagnosed and repaired the remaining 2018 identity and tyre-parser
  failures; verified 105/105 completed sessions and removed legacy false `nan`
  identities.
- 2026-07-28 — Committed the reviewed cache-aware FastF1 request-budget and
  detailed-progress phase locally without pushing.
- 2026-07-28 — Implemented and verified cache-aware FastF1 request accounting,
  the rolling safety budget, detailed job execution state, cooldown countdowns,
  and event-grouped dashboard progress.
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
