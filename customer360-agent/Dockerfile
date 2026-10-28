# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt
# Pull the OpenTelemetry instrumentors (FastAPI/SQLAlchemy/urllib/...) for the
# zero-code tracing entrypoint below. No-op at runtime unless OTEL_SDK_DISABLED=false.
RUN opentelemetry-bootstrap -a install

COPY src ./src

EXPOSE 8009

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8009/health', timeout=3)" || exit 1

CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8009"]
