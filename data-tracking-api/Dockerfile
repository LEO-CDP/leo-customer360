# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt
# Install the OpenTelemetry instrumentors matching the installed libs (FastAPI,
# Redis, boto3, ...). No-op at runtime unless OTEL_SDK_DISABLED=false.
RUN opentelemetry-bootstrap -a install

COPY . .

EXPOSE 8010

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health', timeout=3)" || exit 1

# Launch under opentelemetry-instrument so traces export over OTLP when enabled
# (OTEL_* env from deployments/lib/otel.sh; a no-op when OTEL_SDK_DISABLED=true).
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
