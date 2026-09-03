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

# Install each code location's own requirements if present.
RUN for d in identity_resolution scoring segmentation analytics data_synch email_engine notification_engine campaign_activation personalization; do \
      if [ -f "$d/requirements.txt" ]; then pip install --no-cache-dir -r "$d/requirements.txt"; fi; \
    done

ENV DAGSTER_HOME=/dagster_home
RUN mkdir -p /dagster_home
# Bake the Dagster instance config (Postgres run/event/schedule storage) into
# DAGSTER_HOME. On the VM/Docker path this is the only instance config; in k8s
# there is no dagster-home PVC anymore, so this baked file is used directly.
# See deployments/docs/dagster-scaling-analysis.md §4.
COPY dagster.yaml /dagster_home/dagster.yaml
# boto3 config forcing S3 path-style addressing, for the S3 compute-log manager
# (Phase-0 sub-step) against MinIO / vStorage. Only takes effect when
# AWS_CONFIG_FILE points here — set in the k8s deployment, NOT on the VM path
# (which keeps local compute logs). App S3 clients set their own addressing
# style explicitly, so an explicit boto3 Config overrides this and they are
# unaffected.
RUN printf '[default]\ns3 =\n    addressing_style = path\n' > /app/aws-config

EXPOSE 3000
CMD ["dagster", "dev", "-w", "workspace.yaml", "-h", "0.0.0.0", "-p", "3000"]

# ci: trigger image build — GHCR smoke test 2026-08-20

# ci/cd: redeploy trigger 2026-08-20 #3 (no functional change)
