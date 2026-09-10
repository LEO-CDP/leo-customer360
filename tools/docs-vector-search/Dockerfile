FROM python:3.12-slim
WORKDIR /app

# llama-cpp-python compiles native code; fastembed's onnxruntime is a wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install OpenTelemetry auto-instrumentation for the detected packages (FastAPI, psycopg, …)
# so `opentelemetry-instrument` (see CMD) traces requests with zero code changes.
RUN opentelemetry-bootstrap -a install

# Pre-bake the default embed + rerank models into the image so the FIRST boot doesn't
# download them at runtime — unauthenticated Hugging Face is ~10 min on the box, which
# blew past the healthcheck. Baked OUTSIDE the mounted MODELS_DIR (a host volume would
# shadow anything under /app/models). Keep these two names in sync with the config.py /
# deploy defaults (DOCS_EMBEDDING_MODEL, DOCS_RERANK_MODEL); an env override still downloads on demand.
ENV FASTEMBED_CACHE=/app/model-cache/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/model-cache/fastembed')" \
 && python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder(model_name='BAAI/bge-reranker-base', cache_dir='/app/model-cache/fastembed')"

COPY src ./src

# Corpus is bind-mounted; the Qwen GGUF lives in a mounted volume (fetched by the deploy
# step). The fastembed embed/rerank models are baked above (FASTEMBED_CACHE), not mounted.
ENV CORPUS_DIR=/app/corpus \
    MODELS_DIR=/app/models

EXPOSE 8000
# Serve only — the index is built by the deploy step (docker compose run ... enrich).
# opentelemetry-instrument wraps uvicorn for zero-code request tracing; it is a near-no-op when
# OTEL_SDK_DISABLED=true (the UAT default). enrich overrides CMD (`python -m src.enrich`), so
# only the long-lived server process is traced, not the batch index build.
CMD ["opentelemetry-instrument", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
