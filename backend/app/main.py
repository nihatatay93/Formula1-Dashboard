import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.router import api_v1_router
from app.db.schema import (
    DatabaseSchemaMismatchError,
    verify_database_schema,
)
from app.live.api import compat_router as live_compat_router
from app.live.state import get_live_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the live-timing retention loop for the lifetime of the process.

    No live session is started here; collection is on demand. Startup only
    schedules the retention sweep for the disposable session logs.
    """
    live_service = get_live_service()
    await live_service.startup()
    try:
        yield
    finally:
        await live_service.shutdown()


app = FastAPI(
    title="Formula1 Dashboard API",
    description="Backend API for Formula1 Dashboard.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_v1_router)
# Root-level /auth, for the FastF1 companion extension's fixed contract.
app.include_router(live_compat_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Formula1 Dashboard API",
        "status": "scaffold",
    }


@app.get("/api/health/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/api/health/ready")
def readiness() -> JSONResponse:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": {"database": "not_configured"},
            },
        )

    try:
        with psycopg.connect(database_url, connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                verify_database_schema(cursor)
    except DatabaseSchemaMismatchError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": {"database": "schema_mismatch"},
            },
        )
    except psycopg.Error:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": {"database": "unavailable"},
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "checks": {"database": "ready"},
        },
    )
