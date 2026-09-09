import threading
import uuid
from .config import DATA, SEMANTIC

class VectorCache:
    def __init__(self):
        self.status = 'loading' if SEMANTIC else 'disabled'
        self.client = None
        self.model = None
        self.lock = threading.Lock()

    def initialize(self):
        if not SEMANTIC: return
        try:
            from fastembed import TextEmbedding
            from qdrant_client import QdrantClient, models
            self.model = TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2',cache_dir=str(DATA / 'models'),local_files_only=True)
            self.client = QdrantClient(path=str(DATA / 'qdrant'))
            if not self.client.collection_exists('queries'):
                self.client.create_collection('queries',vectors_config=models.VectorParams(size=384,distance=models.Distance.COSINE))
            self.status = 'ready'
        except Exception:
            self.status = 'setup_required'

    def search(self, query):
        if self.status != 'ready': return []
        try:
            with self.lock:
                vector = list(self.model.embed([query]))[0].tolist()
                return [x.payload['cache_id'] for x in self.client.query_points('queries',query=vector,score_threshold=0.90,limit=50).points]
        except Exception:
            self.status = 'unavailable'
            return []

    def put(self, ident, query):
        if self.status != 'ready': return
        try:
            from qdrant_client import models
            with self.lock:
                vector = list(self.model.embed([query]))[0].tolist()
                self.client.upsert('queries',points=[models.PointStruct(id=str(uuid.UUID(ident[:32])),vector=vector,payload={'cache_id':ident})])
        except Exception:
            self.status = 'unavailable'

    def prune(self, ids):
        if self.status != 'ready': return
        from qdrant_client import models
        with self.lock:
            points, offset = self.client.scroll('queries',limit=256,with_vectors=False)
            while points:
                expired = [p.id for p in points if p.payload['cache_id'] not in ids]
                if expired: self.client.delete('queries',points_selector=models.PointIdsList(points=expired))
                if offset is None: break
                points, offset = self.client.scroll('queries',limit=256,offset=offset,with_vectors=False)
