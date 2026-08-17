# Formula1 Dashboard

Formula1 Dashboard is a local-first Formula 1 data platform. It combines the
historical FastF1 archive and SignalR live timing behind a single API, serving
a web dashboard today and a planned iOS application later. Everything runs on
your own machine: nothing is sent anywhere, and no account is needed to read
the archive.

The repository contains the local development scaffold, eight database
migrations, the historical backfill API, session exploration, live timing, and
the analysis views built on top of them:

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
- Read-only historical session detail, result, and paginated lap-summary APIs
- Snapshot-safe session dashboard with explicit two-participant lap analysis
- Vitest component coverage and Playwright desktop/mobile browser workflows
- Reproducible FastF1 lap-telemetry measurement and PostgreSQL-first storage decision
- Docker Compose health checks

Historical telemetry, live timing, championship standings, race pace, driver
comparison, and tyre strategy are implemented. See the change log in
[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) for what landed when.

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
API and worker start. When a running bind-mounted development stack receives a
new migration, apply it before using the reloaded application code:

```bash
docker compose run --rm migrate /opt/venv/bin/alembic upgrade head
```

API readiness and worker startup reject a database whose Alembic head does not
match the checked-out application migrations.

The services are exposed locally at:

- Dashboard: `http://localhost:5173`
- Backend API: `http://localhost:8000`
- API liveness: `http://localhost:8000/api/health/live`
- API readiness: `http://localhost:8000/api/health/ready`
- Season overview: `GET http://localhost:8000/api/v1/seasons/{season_year}`
- Ensure season backfill: `POST http://localhost:8000/api/v1/seasons/{season_year}/backfill`
- Backfill progress: `GET http://localhost:8000/api/v1/backfill-jobs/{job_id}`
- Local FastF1 request usage: `GET http://localhost:8000/api/v1/upstreams/fastf1/usage`
- Session detail: `GET http://localhost:8000/api/v1/sessions/{session_id}`
- Session results: `GET http://localhost:8000/api/v1/sessions/{session_id}/results`
- Entry laps: `GET http://localhost:8000/api/v1/sessions/{session_id}/entries/{session_entry_id}/laps`
- PostgreSQL: `localhost:5432`

The POST command may synchronously refresh the selected season schedule through
FastF1's persistent cache. Session ingestion continues asynchronously in the
single-concurrency worker.

The dashboard defaults to the current UTC season and supports every configured
season from 2018 onward. It reads season coverage, displays event and
session-level archive state, starts or reuses an idempotent backfill, and polls
active job progress every two seconds. The progress view identifies the current,
next, and last-completed Grand Prix session, groups child work by event, and
shows a live countdown for pacing, retry, rate-limit, or local request-budget
waits. A separately polled budget panel displays observed archive/schedule
cache-miss requests in the rolling hour against the 400-warning and
450-application-pause thresholds. This is explicitly a local estimate, not an
authoritative upstream quota. The dashboard never requests full telemetry.
Completed sessions can be opened in an in-page workspace with classification,
driver-specific paginated lap summaries, and a manual two-participant pace
comparison. Only explicitly selected timed laps contribute to an average;
quality warnings remain visible, and a changed archive snapshot clears
incompatible selections rather than mixing data.

Run frontend unit/component tests and browser interactions:

```bash
npm test --prefix frontend
npm run test:e2e --prefix frontend
```

Stop the stack:

```bash
docker compose down
```

## Live timing (replay)

Live timing is a separate ephemeral path: frames are streamed and logged outside
the archive, never stored as sporting data, and deleted after a retention
window. No live SignalR client exists yet, so the dashboard's Live Timing view
reports an unconfigured feed by default.

### F1 TV account

A live SignalR connection needs an F1 TV subscription. In the Live Timing view:

1. Install the
   [FastF1 companion extension](https://github.com/theOehrly/fastf1-companion)
   once.
2. Click **Sign in with Formula 1**, log in, then click **Connect**.

Pasting the `login-session` cookie by hand is available as a fallback. No
password is ever sent to this application, and the stored token is never
returned to the browser.

### Replay

To drive it from a recorded session, put a recording in `recordings/` and start
the API with it selected:

```bash
LIVE_TIMING_REPLAY_PATH=/recordings/<file>.jsonl LIVE_TIMING_REPLAY_SPEED=2 docker compose up -d api
```

Recordings are gitignored. Design and rationale are in
[`docs/LIVE_TIMING_DESIGN.md`](docs/LIVE_TIMING_DESIGN.md).

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

PostgreSQL authenticates with scram-sha-256 and is bound to loopback. The
local development password in `compose.yaml` is a development credential, not
a secret; a deployment supplies `POSTGRES_PASSWORD` from its own secret store.
See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Project context

Read [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) before making changes.
It is the authoritative record of implemented behavior and accepted decisions.
Telemetry evidence and the TimescaleDB review triggers are recorded in
[`docs/TELEMETRY_STORAGE_DECISION.md`](docs/TELEMETRY_STORAGE_DECISION.md).

## Licence

Released under the [MIT Licence](LICENSE).

## Disclaimer

This is an unofficial, non-commercial personal project. It is not associated
with, endorsed by, or affiliated with Formula 1, Formula One Digital Media
Limited, the FIA, or any Formula 1 team.

"Formula 1", "F1", "FORMULA ONE", "GRAND PRIX" and related marks are
trademarks of Formula One Licensing BV. They are used here only to describe
the sport the data concerns.

Timing and telemetry data is read through [FastF1](https://github.com/theOehrly/Fast-F1),
which sources it from publicly available Formula 1 feeds, and is subject to
those feeds' own terms. Nothing here redistributes that data: the archive is
built locally, on your own machine, from your own requests. No team logo,
driver photograph, or other licensed media is included or fetched.
