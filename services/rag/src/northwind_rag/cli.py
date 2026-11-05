"""`make ingest` and `make ingest-enriched`: load data/fixtures into an index (RAG-01, RAG-02)."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from northwind_rag.embedding import FastEmbedEmbedder
from northwind_rag.ingest import SHARED_DATABASE, ingest, ingest_enriched

DEFAULT_DATABASE_URL = "postgresql://northwind:northwind-local@localhost:5432/northwind"
DEFAULT_ENRICHED_DATABASE_URL = (
    "postgresql://northwind:northwind-local@localhost:5432/northwind_enriched"
)


def enriched_database_url() -> str:
    """Its own variable: nothing that sets DATABASE_URL can point this at the shared index."""
    return os.environ.get("ENRICHED_DATABASE_URL", DEFAULT_ENRICHED_DATABASE_URL)


def create_enriched_database(url: str) -> None:
    """Creates the database and its vector extension if they are missing."""
    settings = conninfo_to_dict(url)
    name = str(settings.get("dbname"))
    if name == SHARED_DATABASE:
        raise SystemExit(f"ENRICHED_DATABASE_URL must not name {SHARED_DATABASE!r}")
    admin_url = make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        found = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if found is None:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("data/fixtures"))
    parser.add_argument("--enriched", action="store_true", help="the isolated enriched index")
    args = parser.parse_args()
    try:
        if args.enriched:
            url = enriched_database_url()
            create_enriched_database(url)
        else:
            url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        connection = psycopg.connect(url)
    except psycopg.Error:
        # libpq echoes parts of a malformed URL, password included (hard rule 4).
        raise SystemExit("could not connect to the database; check DATABASE_URL") from None
    with connection as conn:
        run = ingest_enriched if args.enriched else ingest
        report = run(conn, args.fixtures, FastEmbedEmbedder())
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
