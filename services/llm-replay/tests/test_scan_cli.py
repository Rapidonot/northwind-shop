"""CC-27: `make scan-cassettes` survives a key file it may not read.

Claude's sandbox denies reads of the key file, and on macOS even a stat of it raises
PermissionError. The scan then runs as it does in CI, regex only, and says so.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from northwind_llm_replay.cli import _secret, cmd_scan
from northwind_llm_replay.proxy import load_api_key

pytestmark = pytest.mark.req("CC-27")

KEY_FILE = "." + "env"
FAKE_KEY = "sk-ant-" + "scanclifake0000000000"


class DeniedStat(type(Path())):  # type: ignore[misc]
    """A path whose stat fails the way the macOS sandbox fails it."""

    def exists(self, *, follow_symlinks: bool = True) -> bool:
        raise PermissionError(1, "Operation not permitted", str(self))


def unreadable_key_file(tmp_path: Path) -> Path:
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    path = tmp_path / KEY_FILE
    path.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    path.chmod(0)
    return path


def test_denied_stat_means_no_secret(tmp_path: Path) -> None:
    assert _secret(DeniedStat(tmp_path / KEY_FILE)) is None


def test_denied_read_means_no_secret(tmp_path: Path) -> None:
    assert _secret(unreadable_key_file(tmp_path)) is None


def test_proxy_reports_a_denied_key_file_as_no_key(tmp_path: Path) -> None:
    """RuntimeError is what the proxy turns into a refusal that releases the reservation."""
    with pytest.raises(RuntimeError, match="cannot read"):
        load_api_key(DeniedStat(tmp_path / KEY_FILE))


def test_readable_key_file_still_gives_the_secret(tmp_path: Path) -> None:
    path = tmp_path / KEY_FILE
    path.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    assert _secret(path) == FAKE_KEY


def test_scan_reports_regex_only_when_the_key_file_is_denied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "clean.json"
    clean.write_text("{}\n")
    args = argparse.Namespace(paths=[str(clean)], env_file=unreadable_key_file(tmp_path))
    assert cmd_scan(args) == 0
    assert "regex only (no readable key file)" in capsys.readouterr().out
