FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
    MODEL_CACHE_DIR=/app/data/models
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY backend ./backend
COPY scripts ./scripts
COPY migrations ./migrations
RUN python scripts/setup_vectors.py && rm -f data/models/minilm.tar.gz \
    && useradd --create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
