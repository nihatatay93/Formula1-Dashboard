from functools import cache
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import Cursor


class DatabaseSchemaMismatchError(RuntimeError):
    """Raised when the database is not at the application's Alembic head."""


@cache
def expected_schema_heads() -> frozenset[str]:
    backend_root = Path(__file__).resolve().parents[2]
    configuration = Config(str(backend_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(configuration)
    return frozenset(scripts.get_heads())


def verify_database_schema(cursor: Cursor[tuple[object, ...]]) -> None:
    try:
        cursor.execute("SELECT version_num FROM alembic_version")
    except psycopg.errors.UndefinedTable as error:
        raise DatabaseSchemaMismatchError(
            "database schema revision does not match application migrations"
        ) from error

    database_heads = frozenset(str(row[0]) for row in cursor.fetchall())
    if database_heads != expected_schema_heads():
        raise DatabaseSchemaMismatchError(
            "database schema revision does not match application migrations"
        )
