"""Bounded ingestion job for Actions. It never runs a web server."""
import asyncio
import os
import time

from backend.catalog import import_catalog
from backend.engine import Engine
from backend.media import NAMES
from backend.postgres import create_store
from backend.pgvector import PostgresVectors


async def main():
    store = create_store()
    if not hasattr(store, "purge"):
        raise RuntimeError("Scheduled collection requires DATABASE_URL.")
    try:
        with store.lease("worldbrief:scheduled-collection") as held:
            if not held:
                print("Another collection is running; skipped.")
                return
            country = store.meta("country", os.getenv("NEWS_COUNTRY", "IN"))
            if country not in NAMES:
                raise RuntimeError("Configure a valid NEWS_COUNTRY.")
            store.purge()
            import_catalog(store, country)
            vector = PostgresVectors(store)
            await asyncio.to_thread(vector.initialize)
            engine = Engine(store, vector)
            result = await engine.query(dict(country=country, query="", topic="All", period="day",
                timezone="UTC", count=50, refresh=False, phase="expanded"))
            # Keep this comfortably inside the GitHub Actions job budget. The
            # next scheduled run picks up the remaining articles, while the API
            # can still prepare a small missing batch during a search.
            vector_limit = max(1, min(500, int(os.getenv("SCHEDULED_VECTOR_LIMIT", "100"))))
            pending = store.rows("""SELECT a.* FROM articles a
                LEFT JOIN article_vectors v ON v.article_id=a.id
                ORDER BY (v.article_id IS NULL) DESC, a.published DESC LIMIT ?""", (vector_limit,))
            await asyncio.to_thread(vector.prepare, pending)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                await engine.summaries.process()
                if engine.groq.status in ("missing_key", "daily_budget_reached", "rate_limited"):
                    break
                await asyncio.sleep(5)
            store.purge()
            print(f"Collected {len(result['stories'])} stories for {country}.")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=540))
