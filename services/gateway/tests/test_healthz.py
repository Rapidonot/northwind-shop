import logging

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


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize(
    "url",
    ["postgresql://u:pa%zzsecret@localhost/x", "postgresql://u:hunter2secret@[bad/x"],
)
def test_healthz_keeps_a_malformed_database_url_out_of_errors_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/healthz")  # TestClient re-raises anything the app lets escape
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "unavailable", "pgvector": None}
    assert "secret" not in response.text
    assert "secret" not in caplog.text


@pytest.mark.req("FOUND-0")
@pytest.mark.integration
def test_healthz_reports_db_and_pgvector_with_stack_running() -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["pgvector"], "pgvector extension should be installed by infra/db/init"
