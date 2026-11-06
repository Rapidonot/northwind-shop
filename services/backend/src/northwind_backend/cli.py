"""Writes data/backend/ (FOUND-3): `uv run python -m northwind_backend.cli`."""

from __future__ import annotations

import argparse
from pathlib import Path

from northwind_backend.generate import DEFAULT_SEED, build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("data/fixtures"))
    parser.add_argument("--out", type=Path, default=Path("data/backend"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    # Ingest reads data/fixtures, so nothing here may land in it.
    if args.out.resolve().is_relative_to(args.fixtures.resolve()):
        parser.error("--out must not be inside --fixtures")

    files = build(args.fixtures, args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (args.out / name).write_bytes(body)
    print(f"wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
