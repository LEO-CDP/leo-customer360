FROM python:3.12-slim
WORKDIR /app

# llama-cpp-python compiles native code; fastembed's onnxruntime is a wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# Corpus is bind-mounted; model weights live in a mounted volume (fetched by the
# deploy step) — neither is baked into the image.
ENV CORPUS_DIR=/app/corpus \
    MODELS_DIR=/app/models

EXPOSE 8000
# Serve only — the index is built by the deploy step (docker compose run ... enrich).
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
