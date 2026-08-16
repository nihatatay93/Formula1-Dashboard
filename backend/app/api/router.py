from fastapi import APIRouter

from app.api.backfill_jobs import router as backfill_jobs_router
from app.api.seasons import router as seasons_router
from app.api.sessions import router as sessions_router
from app.api.telemetry import router as telemetry_router
from app.api.upstream_usage import router as upstream_usage_router
from app.auth.api import router as auth_router
from app.live.api import router as live_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(backfill_jobs_router)
api_v1_router.include_router(live_router)
api_v1_router.include_router(seasons_router)
api_v1_router.include_router(sessions_router)
api_v1_router.include_router(telemetry_router)
api_v1_router.include_router(upstream_usage_router)
