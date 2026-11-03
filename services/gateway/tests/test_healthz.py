import pytest
from fastapi.testclient import TestClient

from northwind_gateway.app import app

client = TestClient(app)


@pytest.mark.req("FOUND-0")
def test_healthz_reports_db_unavailable_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 on localhost refuses connections immediately.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none")
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "unavailable", "pgvector": None}


@pytest.mark.req("FOUND-0")
@pytest.mark.integration
def test_healthz_reports_db_and_pgvector_with_stack_running() -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["pgvector"], "pgvector extension should be installed by infra/db/init"
