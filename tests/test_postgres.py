"""Run against a disposable database only; CI supplies TEST_DATABASE_URL."""
import json
import os
import time
import pytest

from backend.postgres import PostgresStore, postgres_sql
from backend.pgvector import PostgresVectors
from backend.store import digest


def test_sql_binding_preserves_literals():
    assert postgres_sql("SELECT '?' WHERE id=?") == "SELECT '?' WHERE id=%s"


@pytest.fixture
def pg(postgres_store):
    return postgres_store


def article(ident, published):
    return dict(id=ident,url=f"https://example.com/{ident}",publisher="Example",title=ident,
                excerpt="Report details",topic="World",region="IN",published=published,
                fetched=time.time(),content_hash=digest(ident),image_url="https://example.com/image.jpg")


def test_storage_upserts_cleanup_and_snapshots(pg):
    now=time.time()
    pg.upsert_articles([article("old",now-10*86400),article("new",now)])
    pg.set_meta("country","US");pg.set_meta("country","IN")
    assert pg.meta("country")=="IN"
    assert pg.upsert_articles([article("new",now)])==0
    with pg.db() as db:
        db.execute("INSERT OR REPLACE INTO saved VALUES(?,?,?)",("old",json.dumps({"title":"saved"}),now))
        db.execute("INSERT INTO article_vectors VALUES(?,?,?,?::vector)",("old","hash","test","["+",".join(["1"]+ ["0"]*383)+"]"))
    pg.put_query("key","query",{}, {},60,"revision")
    assert pg.exact("key","revision")
    pg.purge(now)
    assert len(pg.rows("SELECT * FROM articles"))==1
    assert not pg.rows("SELECT * FROM article_vectors")
    assert len(pg.rows("SELECT * FROM saved"))==1


def test_catalog_queue_and_shared_lock(pg):
    from backend.catalog import import_catalog
    from backend.groq import Groq
    from backend.summary_queue import SummaryQueue
    import_catalog(pg,"IN")
    assert sum(r["selected"] for r in pg.rows("SELECT selected FROM media"))==50
    groq=Groq(pg);groq._record_usage(10);groq._record_usage(7)
    assert groq._daily_tokens_used()==17
    with pg.db() as db:
        db.execute("INSERT INTO story_cache VALUES(?,?,?,?)",("story","v",json.dumps({"id":"story","summary_kind":"excerpt"}),time.time()+60))
    queue=SummaryQueue(pg,groq)
    queue.enqueue([{"id":"story"}]);queue.enqueue([{"id":"story"}],retry_failed=True)
    assert pg.rows("SELECT status FROM summary_jobs")[0]["status"]=="pending"
    with pg.lease("test-lock") as first:
        with pg.lease("test-lock") as second:
            assert first and not second
    with pg.lease("test-lock") as released:
        assert released


def test_pgvector_cosine_and_embedding_reuse(pg):
    import numpy as np
    records=[article("related",time.time()),article("unrelated",time.time())]
    pg.upsert_articles(records)
    calls=[]
    class Model:
        def embed(self,texts):
            for text in texts:
                calls.append(text)
                yield np.array(([0.,1.] if text.startswith("unrelated") else [1.,0.])+[0.]*382)
    vectors=PostgresVectors(pg);vectors.model=Model();vectors.status="ready"
    ranked,method=vectors.rerank("query",records)
    assert [a["id"] for a in ranked]==["related"]
    assert method=="bm25+embeddings"
    vectors.rerank("query",records)
    assert len(calls)==4 # two article embeddings once, two query embeddings
    ident=pg.put_query("key","query",{}, {},60,"r")
    vectors.put(ident,"query")
    assert vectors.search("query")==[ident]


def test_indexing_budget_never_returns_stale_vectors(pg, monkeypatch):
    import numpy as np
    from backend import pgvector
    records=[article("related",time.time())]
    pg.upsert_articles(records)
    class Model:
        def embed(self,texts):
            for text in texts:
                yield np.array([1.]+[0.]*383)
    vectors=PostgresVectors(pg);vectors.model=Model()
    assert vectors.prepare(records)
    records[0]["excerpt"]="Completely changed reporting"
    monkeypatch.setattr(pgvector,"HOSTED",True)
    clock=iter([0,10])
    from types import SimpleNamespace
    monkeypatch.setattr(pgvector,"time",SimpleNamespace(monotonic=lambda:next(clock)))
    ranked,method=vectors.rerank("query",records)
    assert method=="bm25+embeddings_partial"
    assert ranked==[]
