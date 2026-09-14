"""Apply the idempotent PostgreSQL schema using the configured database URL."""
import os
from pathlib import Path


def main():
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL before running migrations.")
    with psycopg.connect(url, connect_timeout=15) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(74132001)")
        for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
            connection.execute(path.read_text(encoding="utf-8"))
            print(f"Applied {path.name}")


if __name__ == "__main__":
    main()
