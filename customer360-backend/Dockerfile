# Dagster orchestrator image for customer360-backend.
# Runs the Dagster webserver or singleton daemon, loading all nine code
# locations in workspace.yaml. Identity resolution is managed by Dagster; there
# is no separate identity-resolution worker image.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY customer360-dao/pyproject.toml customer360-dao/README.md /opt/customer360-dao/
COPY customer360-dao/src /opt/customer360-dao/src
COPY customer360-backend/identity_resolution/requirements.txt /tmp/requirements/identity_resolution.txt
COPY customer360-backend/scoring/requirements.txt /tmp/requirements/scoring.txt
COPY customer360-backend/segmentation/requirements.txt /tmp/requirements/segmentation.txt
COPY customer360-backend/analytics/requirements.txt /tmp/requirements/analytics.txt
COPY customer360-backend/data_synch/requirements.txt /tmp/requirements/data_synch.txt
COPY customer360-backend/email_engine/requirements.txt /tmp/requirements/email_engine.txt
COPY customer360-backend/notification_engine/requirements.txt /tmp/requirements/notification_engine.txt
COPY customer360-backend/campaign_activation/requirements.txt /tmp/requirements/campaign_activation.txt
COPY customer360-backend/personalization/requirements.txt /tmp/requirements/personalization.txt

# Build all wheels once. The local DAO wheel lets each code location resolve
# its leo-customer360-dao requirement without reaching a package index.
RUN mkdir -p /wheels /install \
 && pip wheel --no-cache-dir --wheel-dir=/wheels /opt/customer360-dao \
 && pip wheel --no-cache-dir --wheel-dir=/wheels --find-links=/wheels \
      -r /tmp/requirements/identity_resolution.txt \
      -r /tmp/requirements/scoring.txt \
      -r /tmp/requirements/segmentation.txt \
      -r /tmp/requirements/analytics.txt \
      -r /tmp/requirements/data_synch.txt \
      -r /tmp/requirements/email_engine.txt \
      -r /tmp/requirements/notification_engine.txt \
      -r /tmp/requirements/campaign_activation.txt \
      -r /tmp/requirements/personalization.txt \
      dagster dagster-webserver dagster-graphql dagster-postgres dagster-aws psycopg2-binary \
 && pip install --no-cache-dir --no-index --find-links=/wheels --prefix=/install \
      -r /tmp/requirements/identity_resolution.txt \
      -r /tmp/requirements/scoring.txt \
      -r /tmp/requirements/segmentation.txt \
      -r /tmp/requirements/analytics.txt \
      -r /tmp/requirements/data_synch.txt \
      -r /tmp/requirements/email_engine.txt \
      -r /tmp/requirements/notification_engine.txt \
      -r /tmp/requirements/campaign_activation.txt \
      -r /tmp/requirements/personalization.txt \
      dagster dagster-webserver dagster-graphql dagster-postgres dagster-aws psycopg2-binary

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY customer360-backend /app
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENV DAGSTER_HOME=/dagster_home
RUN mkdir -p /dagster_home
# The instance config ($DAGSTER_HOME/dagster.yaml) is NOT baked — entrypoint.sh
# renders it at start against the pre-provisioned Dagster PostgreSQL database.
# S3 compute logs are required in production via DAGSTER_REQUIRE_S3=true.
#
# boto3 config forcing S3 path-style addressing, used by the S3 compute-log
# manager (no addressing_style config of its own) against MinIO / vStorage. App
# S3 clients set their own explicit boto3 Config, which overrides this, so they
# are unaffected.
RUN printf '[default]\ns3 =\n    addressing_style = path\n' > /app/aws-config
ENV AWS_CONFIG_FILE=/app/aws-config

EXPOSE 3000
# entrypoint.sh renders the production instance config, then exec's the CMD.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["dagster-webserver", "-w", "workspace.yaml", "-h", "0.0.0.0", "-p", "3000"]

# ci: trigger image build — GHCR smoke test 2026-08-20

# ci/cd: redeploy trigger 2026-08-20 #3 (no functional change)
