from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import ErrorResponse, SeasonOverviewResponse
from app.api.dependencies import (
    get_database_session_factory,
    require_supported_season_year,
)
from app.api.errors import ApiError
from app.api.season_overview import read_season_overview

router = APIRouter(prefix="/seasons", tags=["seasons"])


@router.get(
    "/{season_year}",
    response_model=SeasonOverviewResponse,
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Server configuration is invalid.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The database is temporarily unavailable.",
        },
    },
    summary="Read a historical season overview",
)
def get_season_overview(
    response: Response,
    season_year: Annotated[int, Depends(require_supported_season_year)],
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
) -> SeasonOverviewResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_season_overview(
            season_year=season_year,
            session_factory=session_factory,
        )
    except SQLAlchemyError:
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            message="The database is temporarily unavailable.",
        ) from None

