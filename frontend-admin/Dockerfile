# syntax=docker/dockerfile:1.7

# Dockerfile
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy and install dependencies first
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt

# Copy the application code
COPY . .

# Expose the application port
EXPOSE 8890

# Set up health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8890/health', timeout=3)" || exit 1

# Start the application with uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8890"]

# ci: trigger image build — GHCR smoke test 2026-08-20

# ci/cd: redeploy trigger 2026-08-20 #2 (no functional change)
