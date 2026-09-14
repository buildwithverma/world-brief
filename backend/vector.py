import threading
import uuid

from .config import DATA, SEMANTIC


class VectorCache:
    def __init__(self):
        self.status = "loading" if SEMANTIC else "disabled"
        self.client = None
        self.model = None
        self.lock = threading.Lock()

    def initialize(self):
        if not SEMANTIC:
            return

        try:
            from fastembed import TextEmbedding
            from qdrant_client import QdrantClient, models

            self.model = TextEmbedding(
                model_name="sentence-transformers/all-MiniLM-L6-v2",
                cache_dir=str(DATA / "models"),
                local_files_only=True,
            )
            self.client = QdrantClient(path=str(DATA / "qdrant"))
            if not self.client.collection_exists("queries"):
                self.client.create_collection(
                    "queries",
                    vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
                )
            self.status = "ready"
        except Exception:
            self.status = "setup_required"

    def search(self, query):
        if self.status != "ready":
            return []

        try:
            with self.lock:
                vector = self._embed(query)
                points = self.client.query_points(
                    "queries",
                    query=vector,
                    score_threshold=0.90,
                    limit=50,
                ).points
                return [point.payload["cache_id"] for point in points]
        except Exception:
            self.status = "unavailable"
            return []

    def put(self, ident, query):
        if self.status != "ready":
            return

        try:
            from qdrant_client import models

            with self.lock:
                self.client.upsert(
                    "queries",
                    points=[
                        models.PointStruct(
                            id=str(uuid.UUID(ident[:32])),
                            vector=self._embed(query),
                            payload={"cache_id": ident},
                        )
                    ],
                )
        except Exception:
            self.status = "unavailable"

    def prune(self, ids):
        if self.status != "ready":
            return

        from qdrant_client import models

        with self.lock:
            points, offset = self.client.scroll("queries", limit=256, with_vectors=False)
            while points:
                expired = [point.id for point in points if point.payload["cache_id"] not in ids]
                if expired:
                    self.client.delete("queries", points_selector=models.PointIdsList(points=expired))
                if offset is None:
                    break
                points, offset = self.client.scroll(
                    "queries",
                    limit=256,
                    offset=offset,
                    with_vectors=False,
                )

    def rerank(self, query, articles):
        """Cache article embeddings in memory; keep the model off the API event loop."""
        if self.model is None or not articles:
            return articles, "bm25"
        import numpy as np
        from .store import digest
        with self.lock:
            cache = getattr(self, "article_vectors", {})
            texts = [a["title"] + " " + a.get("excerpt", "")[:1600] for a in articles]
            keys = [digest(text) for text in texts]
            missing = dict((key, text) for key, text in zip(keys, texts) if key not in cache)
            if len(cache) + len(missing) > 4000:
                cache.clear()
                missing = dict(zip(keys, texts))
            if missing:
                cache.update(zip(missing, self.model.embed(list(missing.values()))))
            self.article_vectors = cache
            query_vector = np.asarray(self._embed(query))
            norm = np.linalg.norm(query_vector)
            scored = []
            for a, key in zip(articles, keys):
                vector = np.asarray(cache[key])
                similarity = float(np.dot(query_vector, vector) / max(1e-12, norm * np.linalg.norm(vector)))
                scored.append({**a, "semantic_score": similarity})
            scored.sort(key=lambda a: a["semantic_score"], reverse=True)
            # A conservative floor removes weak matches, without promising a fixed count.
            return [a for a in scored if a["semantic_score"] >= .32], "bm25+embeddings"

    def _embed(self, text):
        return list(self.model.embed([text]))[0].tolist()
