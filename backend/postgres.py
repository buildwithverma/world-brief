"""PostgreSQL storage with a small, explicit adapter for the existing SQLite SQL.

Only internal SQL is translated. All values remain bound driver parameters.
Run `python -m scripts.migrate` before starting a PostgreSQL-backed app.
"""
import re
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .store import Store

REPLACEMENTS = {
    "meta": ("key value", "key"),
    "query_cache": ("id exact_key query scope data created expires revision", "id"),
    "article_images": ("article_id url", "article_id"),
    "articles": ("id url publisher title excerpt topic region published fetched content_hash", "id"),
    "saved": ("id data created", "id"),
    "source_catalog": ("country id metadata", "country id"),
}


def postgres_sql(sql):
    match = re.search(r"INSERT OR REPLACE INTO (\w+)\s+VALUES", sql, re.I)
    if match:
        columns, keys = (part.split() for part in REPLACEMENTS[match[1]])
        sql = sql.replace(match[0], f"INSERT INTO {match[1]} ({','.join(columns)}) VALUES")
        sql = sql.strip().rstrip(";") + f" ON CONFLICT ({','.join(keys)}) DO UPDATE SET "
        sql += ",".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    sql = sql.replace("MAX(domestic, excluded.domestic)", "GREATEST(country_articles.domestic, excluded.domestic)")
    sql = re.sub(r"json_extract\((\w+\.data), '\$\.summary_kind'\)", r"(\1::jsonb ->> 'summary_kind')", sql)
    # Leave SQL string literals untouched (including literal question marks).
    return re.sub(r"'([^']|'')*'|\?", lambda m: "%s" if m[0] == "?" else m[0], sql)


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, args=()):
        return self.connection.execute(postgres_sql(sql), args)

    def executemany(self, sql, args):
        with self.connection.cursor() as cursor:
            cursor.executemany(postgres_sql(sql), args)


class PostgresStore(Store):
    def __init__(self, url):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        self.pool = ConnectionPool(url, min_size=0, max_size=5, timeout=10,
            kwargs={"row_factory": dict_row, "prepare_threshold": None,
                    "connect_timeout": 10}, open=True)
        with self.db() as db:
            self._seed_configured_feeds(db)

    @contextmanager
    def db(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL search_path TO worldbrief, public, extensions")
                yield Connection(conn)

    @contextmanager
    def lease(self, name):
        # Transaction-scoped locks also work with Supabase's transaction pooler.
        with self.pool.connection() as conn:
            with conn.transaction():
                acquired = conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS held", (name,)).fetchone()["held"]
                yield acquired

    def purge(self, now=None):
        now = time.time() if now is None else now
        cutoff = now - 9 * 86400
        with self.db() as db:
            limit = max(100, int(os.getenv("MAX_STORED_ARTICLES", "5000")))
            db.execute("""DELETE FROM articles WHERE id IN
                (SELECT id FROM articles ORDER BY published DESC OFFSET ?)""", (limit,))
            for table in ("country_articles", "article_images"):
                db.execute(f"DELETE FROM {table} WHERE NOT EXISTS (SELECT 1 FROM articles WHERE articles.id={table}.article_id)")
                db.execute(f"DELETE FROM {table} WHERE article_id IN (SELECT id FROM articles WHERE published<?)", (cutoff,))
            db.execute("DELETE FROM articles WHERE published<?", (cutoff,))
            db.execute("DELETE FROM query_cache WHERE expires<?", (now,))
            db.execute("DELETE FROM summary_jobs WHERE id IN (SELECT id FROM story_cache WHERE expires<?)", (now,))
            db.execute("DELETE FROM story_cache WHERE expires<?", (now,))
        # Saved stories are independent snapshots and deliberately survive cleanup.

    def close(self):
        self.pool.close()


def create_store():
    from .config import DATABASE_URL
    return PostgresStore(DATABASE_URL) if DATABASE_URL else Store()
