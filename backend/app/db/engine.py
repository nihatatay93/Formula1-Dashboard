from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url


def sqlalchemy_database_url(database_url: str | None = None) -> URL:
    raw_url = database_url or os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError("DATABASE_URL is required")

    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("DATABASE_URL must use PostgreSQL")

    return url.set(drivername="postgresql+psycopg")


@lru_cache
def get_engine() -> Engine:
    return create_engine(sqlalchemy_database_url(), pool_pre_ping=True)
