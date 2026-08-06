# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt

COPY . .

# No HTTP surface on this service (background worker) -- DB connectivity is
# the most meaningful liveness signal. See healthcheck.py.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=5 \
    CMD python healthcheck.py || exit 1

# Default command: continuous polling worker (production / `docker compose up`).
# The `dev` profile's `cir-demo-seed` service overrides `command:` in
# docker-compose.yml to run the one-shot seeding scripts instead, reusing
# this same image.
CMD ["python", "worker.py"]
