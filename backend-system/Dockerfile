# Dagster orchestrator image for backend-system.
# Runs `dagster dev` (webserver + daemon) loading all nine code locations in
# workspace.yaml. Identity resolution is managed by Dagster; there is no
# separate identity-resolution worker image.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Dagster core + Postgres driver. `dagster-postgres` backs the instance's
# run/event/schedule storage (Phase 0); psycopg2 is used by both that and the
# ops that connect to the customer360 schema.
RUN pip install --no-cache-dir \
      dagster dagster-webserver dagster-graphql dagster-postgres dagster-aws psycopg2-binary

COPY . /app
RUN chmod +x /app/entrypoint.sh

# Install each code location's own requirements if present.
RUN for d in identity_resolution scoring segmentation analytics data_synch email_engine notification_engine campaign_activation personalization; do \
      if [ -f "$d/requirements.txt" ]; then pip install --no-cache-dir -r "$d/requirements.txt"; fi; \
    done

ENV DAGSTER_HOME=/dagster_home
RUN mkdir -p /dagster_home
# The instance config ($DAGSTER_HOME/dagster.yaml) is NOT baked — entrypoint.sh
# renders it adaptively at start (Postgres + S3 when reachable, else local
# SQLite + local logs) so the orchestrator always boots with or without those
# backends. See deployments/docs/dagster-scaling-analysis.md §4.
#
# boto3 config forcing S3 path-style addressing, used by the S3 compute-log
# manager (no addressing_style config of its own) against MinIO / vStorage. App
# S3 clients set their own explicit boto3 Config, which overrides this, so they
# are unaffected.
RUN printf '[default]\ns3 =\n    addressing_style = path\n' > /app/aws-config
ENV AWS_CONFIG_FILE=/app/aws-config

EXPOSE 3000
# entrypoint.sh renders the adaptive instance config, then exec's the CMD.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["dagster", "dev", "-w", "workspace.yaml", "-h", "0.0.0.0", "-p", "3000"]

# ci: trigger image build — GHCR smoke test 2026-08-20

# ci/cd: redeploy trigger 2026-08-20 #3 (no functional change)
