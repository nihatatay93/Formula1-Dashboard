import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Formula1 Dashboard API",
    description="Backend API for Formula1 Dashboard.",
    version="0.1.0",
)


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
                cursor.execute("SELECT 1")
                cursor.fetchone()
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

