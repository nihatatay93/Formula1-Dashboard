import json
from unittest.mock import MagicMock

import psycopg

import app.main as main_module
from app.db.schema import DatabaseSchemaMismatchError
from app.main import liveness, readiness, root


def test_root_describes_scaffold() -> None:
    assert root() == {
        "name": "Formula1 Dashboard API",
        "status": "scaffold",
    }


def test_liveness_is_independent_of_database() -> None:
    assert liveness() == {"status": "alive"}


def test_readiness_requires_database_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = readiness()

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready",
        "checks": {"database": "not_configured"},
    }


def test_readiness_requires_compatible_database_schema(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://controlled")
    connection = MagicMock()
    monkeypatch.setattr(
        main_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: connection,
    )

    def reject_schema(_cursor: object) -> None:
        raise DatabaseSchemaMismatchError("controlled mismatch")

    monkeypatch.setattr(
        main_module,
        "verify_database_schema",
        reject_schema,
    )

    response = readiness()

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready",
        "checks": {"database": "schema_mismatch"},
    }


def test_readiness_reports_compatible_database(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://controlled")
    connection = MagicMock()
    verify_schema = MagicMock()
    monkeypatch.setattr(
        main_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    monkeypatch.setattr(
        main_module,
        "verify_database_schema",
        verify_schema,
    )

    response = readiness()

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "ready",
        "checks": {"database": "ready"},
    }
    verify_schema.assert_called_once_with(
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    )


def test_readiness_reports_unavailable_database(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://controlled")

    def fail_connection(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("controlled failure")

    monkeypatch.setattr(
        main_module.psycopg,
        "connect",
        fail_connection,
    )

    response = readiness()

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready",
        "checks": {"database": "unavailable"},
    }
