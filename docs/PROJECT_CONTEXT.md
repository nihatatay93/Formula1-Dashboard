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

The local-development scaffold and the first database migration are implemented. FastF1 ingestion, backfill job execution, sporting data, telemetry, and live timing ingestion are not yet implemented.

Implemented services in `compose.yaml`:

- `db`: PostgreSQL with a persistent `postgres_data` volume and a health check.
- `migrate`: One-shot Alembic service that upgrades the database before the API and worker start.
- `api`: FastAPI application with liveness and database-readiness endpoints.
- `worker`: Separate process built from the backend image. It verifies the database connection and remains alive, but does not process jobs yet.
- `frontend`: React, TypeScript, and Vite application that displays backend readiness.

Implemented supporting infrastructure:

- A persistent `fastf1_cache` Docker volume is declared for the worker.
- The backend uses Python 3.13, `uv`, FastAPI, SQLAlchemy 2, Alembic, psycopg, Uvicorn, pytest, and Ruff.
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
│   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── engine.py
│   │   │   ├── naming.py
│   │   │   └── session.py
│   │   ├── main.py
│   │   └── worker.py
│   ├── tests/
│   │   ├── test_database_integration.py
│   │   ├── test_database_metadata.py
│   │   └── test_health.py
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── uv.lock
├── docs/
│   ├── DATABASE_DESIGN.md
│   └── PROJECT_CONTEXT.md
└── frontend/
    ├── src/
    ├── Dockerfile
    ├── package.json
    ├── package-lock.json
    └── vite.config.ts
```

- `backend/app/`: FastAPI and worker process source.
- `backend/app/db/`: SQLAlchemy metadata, connection configuration, session factory, and Revision 1 models.
- `backend/alembic/`: Alembic environment and reviewed migration revisions.
- `backend/tests/`: Backend tests.
- `frontend/src/`: React dashboard source.
- `docs/`: Architecture, decisions, and persistent project context.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic conventions, migration phases, tables, constraints, indexes, and recovery behavior.
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

### Frontend foundation

- Decision: Use React and TypeScript with Vite for the web dashboard.
- Rationale: Provide a typed, component-based dashboard with a lightweight development server.
- Date: 2026-07-27
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

## Database Model

Alembic revision `20260727_0001` implements the backfill control plane:

- `seasons`: One row per season. `year` is a `SMALLINT` primary key constrained to `>= 1950`. Coverage timestamps support future freshness derivation.
- `events`: Championship events keyed internally by `BIGINT IDENTITY`, linked to `seasons`, and unique by `(season_year, round_number)`.
- `sessions`: Event sessions keyed internally by `BIGINT IDENTITY`, linked to `events`, and unique by `(event_id, session_key)`.
- `session_ingestions`: One persistent ingestion-state row per session, including status, source, provisional/finalized state, attempts, lifecycle timestamps, retry eligibility, heartbeat, and sanitized error fields.
- `backfill_jobs`: UUID year-level jobs linked to a season. A partial unique index on `season_year` for `pending` and `running` rows prevents two active jobs for one year.
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

The accepted next migration phase contains `drivers`, `session_entries`, `session_results`, and `laps`. No sporting-data or telemetry table exists yet.

Planned but not implemented behavior:

- Derive year status from coverage, active jobs, and session state.
- Claim session work with `FOR UPDATE SKIP LOCKED`.
- Apply session-level retries, heartbeats, and lease-based crash recovery.
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

No season, session, backfill, lap, telemetry, or WebSocket endpoints have been implemented.

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

The schema fields required for retries, leases, heartbeats, and crash recovery exist, but their timing policies and worker behavior remain undecided and unimplemented. FastF1 is not yet installed, and the worker does not process jobs.

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

No FastF1 backfill execution, sporting data, telemetry, or live timing feature has been completed.

## Work in Progress

- No active implementation is in progress after Revision 1.

## Next Steps

1. Inspect representative FastF1 session data and settle stable driver identity.
2. Review and implement the sporting-data migration for `drivers`, `session_entries`, `session_results`, and `laps`.
3. Define retry count, backoff, heartbeat, lease, and current-season freshness policies before worker orchestration.
4. Decide whether manual backfill cancellation belongs in the first phase.
5. Implement one idempotent FastF1 session backfill vertical slice.
6. Add season coverage and job-progress REST APIs.
7. Add the basic season selection and progress UI.
8. Measure telemetry volume before deciding on TimescaleDB.
9. Design SignalR live timing and reconciliation separately.

## Run and Test Commands

Verified:

```bash
npm run build --prefix frontend
docker compose config --quiet
docker compose up --build --detach
docker compose run --rm migrate /opt/venv/bin/alembic upgrade head
docker compose run --rm migrate /opt/venv/bin/alembic current
docker compose run --rm migrate /opt/venv/bin/alembic check
docker compose run --rm migrate /opt/venv/bin/alembic downgrade base
```

Backend lint and tests are verified in the pinned uv container without using the ignored host virtual environment:

```bash
docker run --rm -e UV_PROJECT_ENVIRONMENT=/tmp/formula1-dashboard-venv -v "$PWD/backend:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.11.29-python3.13-trixie-slim uv run --frozen ruff check .
docker run --rm -e UV_PROJECT_ENVIRONMENT=/tmp/formula1-dashboard-venv -v "$PWD/backend:/workspace" -w /workspace ghcr.io/astral-sh/uv:0.11.29-python3.13-trixie-slim uv run --frozen pytest
```

Database integration tests additionally require `TEST_DATABASE_URL` and a migrated PostgreSQL database. The full suite passed with eight tests against the isolated Compose database.

## Known Issues and Technical Debt

- The worker is only a readiness scaffold and cannot claim or process jobs.
- FastF1 is not installed.
- TimescaleDB usage has not been decided.
- Job recovery, retry, lease, heartbeat, and locking fields exist, but policies and processing behavior have not been finalized.
- Season/backfill API paths and response schemas have not been finalized.
- FastF1 ingestion time and storage volume have not been measured.
- Live SignalR protocol and reconciliation rules have not been designed.
- PostgreSQL trust authentication is suitable only for the current loopback-bound local environment.
- The ignored host `.venv` is not portable; use the verified container-based backend commands.

## Important Files

- `AGENTS.md`: Mandatory context, safety, language, and user-change preservation rules.
- `docs/PROJECT_CONTEXT.md`: Authoritative record of current behavior, accepted decisions, and next steps.
- `docs/DATABASE_DESIGN.md`: Accepted Alembic layout, relational model, migration phases, idempotency, locking, and recovery design.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `backend/alembic/versions/20260727_0001_backfill_control_plane.py`: Reviewed Revision 1 schema and downgrade.
- `backend/app/db/base.py`: Shared SQLAlchemy metadata and timestamp mixin.
- `backend/app/db/models/`: Revision 1 SQLAlchemy control-plane models.
- `backend/tests/test_database_integration.py`: PostgreSQL constraint and index integration coverage.
- `backend/app/main.py`: FastAPI scaffold and health endpoints.
- `backend/app/worker.py`: Placeholder worker lifecycle.
- `backend/tests/test_health.py`: Backend health endpoint unit tests.
- `frontend/src/App.tsx`: Local readiness dashboard.
- `README.md`: Local development overview and commands.

## Change Log

- 2026-07-27 — Accepted the database proposal and implemented and verified Alembic Revision 1 with six backfill control-plane tables.
- 2026-07-27 — Added the proposed Alembic and database model design with phased migrations and explicit open decisions.
- 2026-07-27 — Corrected repository documentation to English and recorded the implemented local scaffold.
- 2026-07-27 — Created persistent project memory and recorded the initial architecture, backfill requirements, and live timing goals.
