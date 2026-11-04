"""`make data`: write the dataset to a directory (FOUND-1).

Refuses to overwrite fixtures whose content changed while DATA_VERSION stayed the same, because
that would silently invalidate every cassette recorded against them (decision 0001).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from northwind_data.generate import generate
from northwind_data.version import DATA_VERSION, DEFAULT_SEED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="northwind_data", description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    out: Path = args.out

    files = generate(args.seed)
    new = json.loads(files["manifest.json"])
    existing = out / "manifest.json"
    if existing.exists():
        old = json.loads(existing.read_text("utf-8"))
        if old.get("seed") == args.seed:
            if old["data_version"] > DATA_VERSION:
                print(
                    f"error: {existing} is DATA_VERSION {old['data_version']}, newer than the "
                    f"generator's {DATA_VERSION}",
                    file=sys.stderr,
                )
                return 2
            if (
                old["data_version"] == DATA_VERSION
                and old["dataset_sha256"] != new["dataset_sha256"]
            ):
                changed = sorted(n for n, h in new["files"].items() if old["files"].get(n) != h)
                print(
                    f"error: generated output changed ({', '.join(changed)}) but DATA_VERSION "
                    f"is still {DATA_VERSION}. Recordings made against the old data would no "
                    "longer replay. Bump DATA_VERSION in "
                    "data/src/northwind_data/version.py and run `make data` again.",
                    file=sys.stderr,
                )
                return 2
    out.mkdir(parents=True, exist_ok=True)
    for name in sorted(files):
        (out / name).write_bytes(files[name])
    counts = ", ".join(f"{k} {v}" for k, v in new["counts"].items())
    print(
        f"wrote {len(files)} files to {out} (DATA_VERSION {DATA_VERSION}, seed {args.seed}): "
        f"{counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
