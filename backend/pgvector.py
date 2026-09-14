"""Persistent MiniLM vectors; BM25 candidates are reranked with exact cosine search."""
import time
from .config import MODEL_CACHE, SEMANTIC, HOSTED
from .store import digest
from .vector import VectorCache

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def literal(vector):
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


class PostgresVectors(VectorCache):
    def __init__(self, store):
        super().__init__()
        self.store = store

    def initialize(self):
        if not SEMANTIC:
            return
        try:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=MODEL, cache_dir=str(MODEL_CACHE),
                                       local_files_only=True, threads=1)
            self.status = "ready"
        except Exception:
            self.status = "setup_required"

    def put(self, ident, query):
        if self.model is None:
            return
        with self.lock, self.store.db() as db:
            db.execute("""INSERT INTO query_vectors VALUES(?, ?, ?::vector)
                ON CONFLICT(id) DO UPDATE SET model=excluded.model, embedding=excluded.embedding""",
                (ident, MODEL, literal(self._embed(query))))

    def search(self, query):
        if self.model is None:
            return []
        with self.lock:
            vector = literal(self._embed(query))
        return [r["id"] for r in self.store.rows("""SELECT id FROM query_vectors
            WHERE model=? AND 1-(embedding <=> ?::vector)>=0.90
            ORDER BY embedding <=> ?::vector LIMIT 50""", (MODEL, vector, vector))]

    def prune(self, ids):
        self.store.purge()

    def prepare(self, articles, deadline=None):
        """Embed missing articles, normally on the scheduled worker's CPU."""
        if self.model is None or not articles:
            return not articles
        ids = [a["id"] for a in articles]
        existing = {r["article_id"]: r for r in self.store.rows(
            "SELECT article_id, content_hash, model FROM article_vectors WHERE article_id=ANY(?)", (ids,))}
        with self.lock:
            for article in articles:
                text = article["title"] + " " + article.get("excerpt", "")[:1600]
                content_hash = digest(text)
                row = existing.get(article["id"], {})
                if row.get("content_hash") == content_hash and row.get("model") == MODEL:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                vector = literal(self._embed(text))
                with self.store.db() as db:
                    db.execute("""INSERT INTO article_vectors
                        SELECT id, ?, ?, ?::vector FROM articles WHERE id=?
                        ON CONFLICT(article_id) DO UPDATE SET content_hash=excluded.content_hash,
                        model=excluded.model, embedding=excluded.embedding""",
                        (content_hash, MODEL, vector, article["id"]))
        return True

    def rerank(self, query, articles):
        if self.model is None or not articles:
            return articles, "bm25"
        ids = [a["id"] for a in articles]
        complete = self.prepare(articles, deadline=time.monotonic()+3 if HOSTED else None)
        with self.lock:
            query_vector = literal(self._embed(query))
        rows = self.store.rows("""SELECT article_id, content_hash, 1-(embedding <=> ?::vector) AS score
            FROM article_vectors WHERE article_id=ANY(?) AND model=?
            AND 1-(embedding <=> ?::vector)>=0.32 ORDER BY embedding <=> ?::vector""",
            (query_vector, ids, MODEL, query_vector, query_vector))
        by_id = {a["id"]: a for a in articles}
        return [{**by_id[r["article_id"]], "semantic_score": r["score"]} for r in rows if r["content_hash"] == digest(by_id[r["article_id"]]["title"] + " " + by_id[r["article_id"]].get("excerpt", "")[:1600])], "bm25+embeddings" if complete else "bm25+embeddings_partial"
