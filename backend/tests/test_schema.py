from unittest.mock import MagicMock

import psycopg
import pytest

from app.db.schema import (
    DatabaseSchemaMismatchError,
    expected_schema_heads,
    verify_database_schema,
)


def test_database_schema_accepts_exact_application_heads() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (revision,) for revision in expected_schema_heads()
    ]

    verify_database_schema(cursor)

    cursor.execute.assert_called_once_with(
        "SELECT version_num FROM alembic_version"
    )


@pytest.mark.parametrize(
    "database_heads",
    [
        (),
        ("20260728_0005",),
        ("unexpected_revision",),
        ("20260728_0007", "unexpected_revision"),
    ],
)
def test_database_schema_rejects_nonmatching_heads(
    database_heads: tuple[str, ...],
) -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (revision,) for revision in database_heads
    ]

    with pytest.raises(
        DatabaseSchemaMismatchError,
        match="does not match",
    ):
        verify_database_schema(cursor)


def test_database_schema_rejects_unversioned_database() -> None:
    cursor = MagicMock()
    cursor.execute.side_effect = psycopg.errors.UndefinedTable(
        "controlled missing table"
    )

    with pytest.raises(
        DatabaseSchemaMismatchError,
        match="does not match",
    ):
        verify_database_schema(cursor)
