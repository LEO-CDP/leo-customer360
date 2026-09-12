FROM python:3.12-slim AS local-dependencies
WORKDIR /build
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

# Build local-provider wheels in a disposable stage so compilers never reach runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-local.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-local.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install OpenTelemetry auto-instrumentation for the detected packages (FastAPI, psycopg, …)
# so `opentelemetry-instrument` (see CMD) traces requests with zero code changes.
RUN opentelemetry-bootstrap -a install

COPY src ./src

# Corpus and optional local model files are bind-mounted at runtime.
ENV CORPUS_DIR=/app/corpus \
    MODELS_DIR=/app/models

EXPOSE 8001
# Serve only — the index is built by the deploy step (docker compose run ... enrich).
# opentelemetry-instrument wraps uvicorn for zero-code request tracing; it is a near-no-op when
# OTEL_SDK_DISABLED=true (the UAT default). enrich overrides CMD (`python -m src.enrich`), so
# only the long-lived server process is traced, not the batch index build.
CMD ["opentelemetry-instrument", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8001"]

# Optional offline/local-provider image. Build explicitly with `--target local`.
FROM runtime AS local
COPY requirements-local.txt .
COPY --from=local-dependencies /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements-local.txt \
 && rm -rf /wheels

# Pre-bake local embedding and reranking models outside the mounted MODELS_DIR.
ENV FASTEMBED_CACHE=/app/model-cache/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/model-cache/fastembed')" \
 && python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder(model_name='BAAI/bge-reranker-base', cache_dir='/app/model-cache/fastembed')"

# The final/default target is the small hosted-provider production image.
FROM runtime AS hosted
