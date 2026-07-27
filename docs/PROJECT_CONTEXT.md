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

An initial local-development scaffold is present. It does not yet contain database models, Alembic migrations, FastF1 ingestion, backfill jobs, or live timing ingestion.

Implemented services in `compose.yaml`:

- `db`: PostgreSQL with a persistent `postgres_data` volume and a health check.
- `api`: FastAPI application with liveness and database-readiness endpoints.
- `worker`: Separate process built from the backend image. It verifies the database connection and remains alive, but does not process jobs yet.
- `frontend`: React, TypeScript, and Vite application that displays backend readiness.

Implemented supporting infrastructure:

- A persistent `fastf1_cache` Docker volume is declared for the worker.
- The backend uses Python 3.13, `uv`, FastAPI, psycopg, Uvicorn, pytest, and Ruff.
- The frontend uses Node.js 24, npm, React, TypeScript, and Vite.
- PostgreSQL trust authentication is restricted to loopback-bound local development and must not be reused for production.

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
│   ├── app/
│   │   ├── main.py
│   │   └── worker.py
│   ├── tests/
│   │   └── test_health.py
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
- `backend/tests/`: Backend tests.
- `frontend/src/`: React dashboard source.
- `docs/`: Architecture, decisions, and persistent project context.
- `docs/DATABASE_DESIGN.md`: Proposed Alembic conventions, migration phases, tables, constraints, indexes, and recovery behavior.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `AGENTS.md`: Mandatory repository workflow and context rules.

Alembic and database model directories have not been created yet.

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

- Decision: The current proposal separates the control plane, sporting data, and telemetry into independent migration phases.
- Rationale: Validate job orchestration and session ingestion before committing to a high-volume telemetry schema or TimescaleDB.
- Date: 2026-07-27
- Status: proposed

### SQLAlchemy database layer

- Decision: The current proposal uses SQLAlchemy 2-style declarative models with synchronous sessions and psycopg for the first backfill phase.
- Rationale: FastF1 processing is blocking, and one synchronous database model avoids unnecessary dual sync/async infrastructure at the start.
- Date: 2026-07-27
- Status: proposed

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

No application tables, constraints, indexes, SQLAlchemy models, or Alembic migrations have been implemented.

The proposed design is documented in `docs/DATABASE_DESIGN.md`. Its planned migration phases are:

1. Backfill control plane: `seasons`, `events`, `sessions`, `session_ingestions`, `backfill_jobs`, and `backfill_job_sessions`.
2. Sporting data: `drivers`, `session_entries`, `session_results`, and `laps`.
3. Telemetry: deferred until storage volume and query patterns are measured.

Key proposed constraints:

- Unique event identity by `(season_year, round_number)`.
- Unique session identity by `(event_id, session_key)`.
- A partial unique index allowing only one `pending` or `running` backfill job per year.
- Composite job-session identity by `(job_id, session_id)`.
- Named check constraints for status, source, record state, and non-negative attempt counts.

Key proposed behavior:

- Persist session ingestion state independently from job history.
- Derive year status from coverage, active jobs, and session state.
- Claim session work with `FOR UPDATE SKIP LOCKED`.
- Use session-level retries, heartbeats, and lease-based crash recovery.
- Keep TimescaleDB and the telemetry table shape outside the first migration.

All items in this subsection remain proposed until explicitly approved and implemented.

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

Retry counts, backoff, leases, heartbeats, and crash recovery have not been accepted yet. FastF1 is not yet installed, and the worker does not process jobs.

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

No application database schema, Alembic migration, FastF1 backfill, or live timing feature has been completed.

## Work in Progress

- Review and approval of the proposed Alembic and database model design.

## Next Steps

1. Review and approve or revise `docs/DATABASE_DESIGN.md`.
2. Resolve the open decisions listed in that document.
3. Add SQLAlchemy and Alembic dependencies after design approval.
4. Create the model and migration directory structure.
5. Implement and verify the control-plane migration.
6. Implement and verify the sporting-data migration.
7. Implement one idempotent FastF1 session backfill vertical slice.
8. Add season coverage and job-progress REST APIs.
9. Add the basic season selection and progress UI.
10. Measure telemetry volume before deciding on TimescaleDB.
11. Design SignalR live timing and reconciliation separately.

## Run and Test Commands

Verified:

```bash
npm run build --prefix frontend
docker compose config --quiet
```

Documented but not yet fully verified in this workspace:

```bash
docker compose up --build
docker compose down
```

The checked-in backend unit test exists, but the current ignored host `.venv` is not portable and did not execute successfully from the host. Container-based backend test execution still needs to be verified.

## Known Issues and Technical Debt

- No application database schema or Alembic environment exists.
- The worker is only a readiness scaffold and cannot claim or process jobs.
- FastF1 is not installed.
- TimescaleDB usage has not been decided.
- Job recovery, retry, lease, heartbeat, and locking behavior have not been finalized.
- Season/backfill API paths and response schemas have not been finalized.
- FastF1 ingestion time and storage volume have not been measured.
- Live SignalR protocol and reconciliation rules have not been designed.
- PostgreSQL trust authentication is suitable only for the current loopback-bound local environment.
- Backend tests still need container-based verification.

## Important Files

- `AGENTS.md`: Mandatory context, safety, language, and user-change preservation rules.
- `docs/PROJECT_CONTEXT.md`: Authoritative record of current behavior, accepted decisions, and next steps.
- `docs/DATABASE_DESIGN.md`: Proposed Alembic layout, relational model, migration phases, idempotency, locking, and recovery design.
- `compose.yaml`: Local service topology, health checks, and persistent volumes.
- `backend/app/main.py`: FastAPI scaffold and health endpoints.
- `backend/app/worker.py`: Placeholder worker lifecycle.
- `backend/tests/test_health.py`: Backend health endpoint unit tests.
- `frontend/src/App.tsx`: Local readiness dashboard.
- `README.md`: Local development overview and commands.

## Change Log

- 2026-07-27 — Added the proposed Alembic and database model design with phased migrations and explicit open decisions.
- 2026-07-27 — Corrected repository documentation to English and recorded the implemented local scaffold.
- 2026-07-27 — Created persistent project memory and recorded the initial architecture, backfill requirements, and live timing goals.
