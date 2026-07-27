import json

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
