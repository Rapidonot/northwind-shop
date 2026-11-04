"""RAG-01: `make ingest` must not print what is in DATABASE_URL (hard rule 4)."""

from __future__ import annotations

import pytest

from northwind_rag import cli

pytestmark = pytest.mark.req("RAG-01")


@pytest.mark.parametrize(
    "url",
    ["postgresql://u:pa%zzsecret@localhost/x", "postgresql://u:hunter2secret@[bad/x"],
)
def test_malformed_database_url_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("sys.argv", ["northwind_rag.cli"])

    with pytest.raises(SystemExit) as caught:
        cli.main()

    captured = capsys.readouterr()
    assert "secret" not in f"{caught.value}{captured.out}{captured.err}"
    assert caught.value.__suppress_context__
