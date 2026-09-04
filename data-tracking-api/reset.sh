#!/bin/bash
set -e

IMAGE_NAME="data-tracking-api"
CONTAINER_NAME="data-tracking-api"

echo "Stopping and removing existing container (if any)..."
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

echo "Building new docker image..."
docker build -t "$IMAGE_NAME:latest" .

echo "Starting new container..."
docker run -d \
  --name "$CONTAINER_NAME" \
  -p 8080:8080 \
  --restart unless-stopped \
  "$IMAGE_NAME:latest"

echo "Done. Container '$CONTAINER_NAME' is up and running."
docker ps --filter "name=$CONTAINER_NAME"
