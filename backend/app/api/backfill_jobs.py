import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.backfill_job import (
    BackfillJobNotFoundError,
    read_backfill_job,
)
from app.api.contracts import BackfillJobResponse, ErrorResponse
from app.api.dependencies import get_database_session_factory
from app.api.errors import ApiError

router = APIRouter(prefix="/backfill-jobs", tags=["backfill-jobs"])


@router.get(
    "/{job_id}",
    response_model=BackfillJobResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "The backfill job does not exist.",
        },
        500: {
            "model": ErrorResponse,
            "description": "Server configuration is invalid.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The database is temporarily unavailable.",
        },
    },
    summary="Read backfill job progress",
)
def get_backfill_job(
    response: Response,
    job_id: uuid.UUID,
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
) -> BackfillJobResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_backfill_job(
            job_id=job_id,
            session_factory=session_factory,
        )
    except BackfillJobNotFoundError:
        raise ApiError(
            status_code=404,
            code="backfill_job_not_found",
            message="Backfill job was not found.",
        ) from None
    except SQLAlchemyError:
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            message="The database is temporarily unavailable.",
        ) from None
