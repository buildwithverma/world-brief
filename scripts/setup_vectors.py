import os
os.environ['HF_HUB_DISABLE_XET']='1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'
from pathlib import Path
import httpx
from huggingface_hub import set_client_factory
set_client_factory(lambda:httpx.Client(trust_env=False,follow_redirects=True,timeout=120))
from fastembed import TextEmbedding

root=Path(__file__).resolve().parent.parent
try:
    model=TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2',cache_dir=str(root/'data'/'models'),local_files_only=True)
except Exception:
    # Official FastEmbed model metadata supplies this alternate when HF is unavailable.
    import tarfile
    directory=root/'data'/'models'
    directory.mkdir(parents=True,exist_ok=True)
    archive=directory/'minilm.tar.gz'
    if not archive.exists():
        response=httpx.get('https://storage.googleapis.com/qdrant-fastembed/sentence-transformers-all-MiniLM-L6-v2.tar.gz',trust_env=False,timeout=120,follow_redirects=True)
        response.raise_for_status()
        archive.write_bytes(response.content)
    with tarfile.open(archive) as tar:
        tar.extractall(directory,filter='data')
    model=TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2',cache_dir=str(directory),local_files_only=True)
vector=list(model.embed(['World news briefing']))[0]
print(f'Local embedding model ready: {len(vector)} dimensions. Restart the backend to enable semantic caching.')
