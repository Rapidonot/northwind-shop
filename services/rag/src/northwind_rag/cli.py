"""`make ingest`: loads data/fixtures into the index (RAG-01)."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

import psycopg

from northwind_rag.embedding import FastEmbedEmbedder
from northwind_rag.ingest import ingest

DEFAULT_DATABASE_URL = "postgresql://northwind:northwind-local@localhost:5432/northwind"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("data/fixtures"))
    args = parser.parse_args()
    try:
        connection = psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    except psycopg.Error:
        # libpq echoes parts of a malformed URL, password included (hard rule 4).
        raise SystemExit("could not connect to the database; check DATABASE_URL") from None
    with connection as conn:
        report = ingest(conn, args.fixtures, FastEmbedEmbedder())
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
