# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY customer360-dao /opt/customer360-dao
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install /opt/customer360-dao

# The agent's HTTP client library (leo_customer360_agent.client). Depends on the DAO
# above; src-layout packages ONLY the client, not the agent server.
COPY customer360-agent /opt/customer360-agent
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install /opt/customer360-agent

COPY customer360-api/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt
RUN opentelemetry-bootstrap -a install

COPY customer360-api/ .

EXPOSE 8008

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8008/health', timeout=3)" || exit 1

CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8008"]

# ci: trigger image build — GHCR smoke test 2026-08-20

# ci/cd: redeploy trigger 2026-08-20 #3 (no functional change)
