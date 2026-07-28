from fastapi import APIRouter

from app.api.backfill_jobs import router as backfill_jobs_router
from app.api.seasons import router as seasons_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(backfill_jobs_router)
api_v1_router.include_router(seasons_router)
