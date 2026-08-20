# Dagster orchestrator image for backend-system (used by k8s/base/dagster.yaml).
# Runs `dagster dev` (webserver + daemon) loading the 7 code locations in
# workspace.yaml. There is no compose service for this; it was host-run before.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Dagster core + Postgres driver (ops connect to the customer360 schema).
RUN pip install --no-cache-dir \
      dagster dagster-webserver dagster-graphql psycopg2-binary

COPY . /app

# Install each code location's own requirements if present.
RUN for d in identity_resolution scoring segmentation analytics data_synch email_engine notification_engine; do \
      if [ -f "$d/requirements.txt" ]; then pip install --no-cache-dir -r "$d/requirements.txt"; fi; \
    done

ENV DAGSTER_HOME=/dagster_home
RUN mkdir -p /dagster_home

EXPOSE 3000
CMD ["dagster", "dev", "-w", "workspace.yaml", "-h", "0.0.0.0", "-p", "3000"]

# ci: trigger image build — GHCR smoke test 2026-08-20
