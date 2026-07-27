# Formula1 Dashboard

Formula1 Dashboard is a local-first Formula 1 data platform. It will combine
historical FastF1 data and future SignalR live timing behind a single API for
the web dashboard and a future iOS application.

The repository is currently limited to the initial development scaffold:

- FastAPI backend with liveness and PostgreSQL readiness endpoints
- Separate worker process using the same backend image
- React, TypeScript, and Vite frontend
- PostgreSQL
- Docker Compose health checks

Database models, migrations, backfill behavior, and live timing ingestion are
not implemented yet.

## Requirements

- Docker with Docker Compose

Node.js 24 and CPython 3.13 are needed only when running services directly
outside Docker.

## Local development

Start the complete local stack:

```bash
docker compose up --build
```

The services are exposed locally at:

- Dashboard: `http://localhost:5173`
- Backend API: `http://localhost:8000`
- API liveness: `http://localhost:8000/api/health/live`
- API readiness: `http://localhost:8000/api/health/ready`
- PostgreSQL: `localhost:5432`

Stop the stack:

```bash
docker compose down
```

PostgreSQL uses trust authentication only for this loopback-bound local
development scaffold. This configuration must not be reused for production.

## Project context

Read [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) before making changes.
It is the authoritative record of implemented behavior and accepted decisions.

