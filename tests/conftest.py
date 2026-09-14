import os
import pytest
from backend.postgres import PostgresStore

@pytest.fixture
def postgres_store():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    store = PostgresStore(url)
    # Explicit test-only URL. Never use the live deployment DB for this suite.
    with store.db() as db:
        db.execute("TRUNCATE articles, article_images, country_articles, media, source_catalog, story_cache, query_cache, summary_jobs, saved, meta, usage CASCADE")
    yield store
    store.close()
