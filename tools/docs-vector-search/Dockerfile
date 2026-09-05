FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY data ./data

# The corpus is bind-mounted (like the reference vault-graph mounts its vault),
# so live doc edits show up without rebuilding the image. Point CORPUS_DIR at it.
ENV CORPUS_DIR=/app/corpus \
    DATA_DIR=/app/data

EXPOSE 8000
# Build/refresh the index once against the mounted corpus, then serve.
CMD ["sh", "-c", "python -m src.enrich && uvicorn src.server:app --host 0.0.0.0 --port 8000"]
