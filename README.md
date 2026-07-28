# Formula1 Dashboard

Formula1 Dashboard is a local-first Formula 1 data platform. It will combine
historical FastF1 data and future SignalR live timing behind a single API for
the web dashboard and a future iOS application.

The repository currently contains the local development scaffold, three
database migrations, and the first historical backfill API slice:

- FastAPI backend with liveness and PostgreSQL readiness endpoints
- Single-concurrency archive worker using the same backend image
- React, TypeScript, and Vite season archive dashboard
- PostgreSQL with SQLAlchemy 2 models and Alembic migrations
- Backfill control-plane tables for seasons, sessions, ingestion state, and jobs
- Sporting-data tables for drivers, session entries, results, and lap summaries
- Locked FastF1 runtime and pure results-and-laps normalization
- Serialized, cache-backed FastF1 one-session loader
- Atomic persistence and stale-row replacement for one normalized archive session
- A database-bound one-session loading, normalization, and persistence vertical slice
- Observable pending/running/completed/failed archive attempts with sanitized errors
- Validated runtime settings, retry classification, and equal-jitter backoff calculations
- Transactional job-session claiming and synchronized archive retry transitions
- Ownership-fenced heartbeat updates and atomic claimed archive completion
- Bounded stale-lease recovery with retry-budget and stale-worker fencing
- Deterministic season-coverage and archive-correction eligibility decisions
- Cache-backed FastF1 schedule discovery with real session start/end boundaries
- Atomic calendar refresh and latest-snapshot membership tracking
- Advisory-locked active-job creation/reuse with idempotent session queuing
- Transactional parent-job aggregation from locked session outcomes
- Worker claim, heartbeat, recovery, failure/completion, and aggregation loop
- Idempotent season backfill command with synchronous cached schedule discovery
- Read-only season overview and backfill-job progress APIs
- Stable versioned response, validation, and sanitized error contracts
- Docker Compose health checks

Historical session-result and lap-summary APIs, telemetry, and live timing
ingestion are not implemented yet.

## Requirements

- Docker with Docker Compose

Node.js 24 and CPython 3.13 are needed only when running services directly
outside Docker.

## Local Python editor setup

VS Code and other host editors need a native virtual environment; a `.venv`
created inside the Linux backend container is not usable on macOS.

Install the required host tools and synchronize the locked backend environment:

```bash
brew install python@3.13 uv
cd backend
uv venv --clear --python 3.13 .venv
uv sync --frozen
```

When the repository root is open in VS Code, select
`backend/.venv/bin/python` with **Python: Select Interpreter**, then reload the
window if import diagnostics remain cached. The generated `.venv` and local
`.vscode` settings are ignored by Git.

## Local development

Start the complete local stack:

```bash
docker compose up --build
```

The one-shot `migrate` service applies repository Alembic migrations before the
API and worker start.

The services are exposed locally at:

- Dashboard: `http://localhost:5173`
- Backend API: `http://localhost:8000`
- API liveness: `http://localhost:8000/api/health/live`
- API readiness: `http://localhost:8000/api/health/ready`
- Season overview: `GET http://localhost:8000/api/v1/seasons/{season_year}`
- Ensure season backfill: `POST http://localhost:8000/api/v1/seasons/{season_year}/backfill`
- Backfill progress: `GET http://localhost:8000/api/v1/backfill-jobs/{job_id}`
- PostgreSQL: `localhost:5432`

The POST command may synchronously refresh the selected season schedule through
FastF1's persistent cache. Session ingestion continues asynchronously in the
single-concurrency worker.

The dashboard defaults to the current UTC season and supports every configured
season from 2018 onward. It reads season coverage, displays event and
session-level archive state, starts or reuses an idempotent backfill, and polls
active job progress every two seconds. It never requests full telemetry.

Stop the stack:

```bash
docker compose down
```

## Database migrations

Apply all migrations:

```bash
docker compose run --rm migrate /opt/venv/bin/alembic upgrade head
```

Show the current revision and check for model/schema drift:

```bash
docker compose run --rm migrate /opt/venv/bin/alembic current
docker compose run --rm migrate /opt/venv/bin/alembic check
```

Downgrades can remove data. Review the target revision and migration before
running a downgrade.

PostgreSQL uses trust authentication only for this loopback-bound local
development scaffold. This configuration must not be reused for production.

## Project context

Read [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) before making changes.
It is the authoritative record of implemented behavior and accepted decisions.
