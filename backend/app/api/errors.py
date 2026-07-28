from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTP failure with the stable client-safe API error envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message},
            headers=dict(headers) if headers is not None else None,
        )

    detail: dict[str, Any]

