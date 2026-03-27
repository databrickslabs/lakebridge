#!/usr/bin/env bash
set -Eeuo pipefail

# Config
SPARK_CONTAINER="${SPARK_CONTAINER:-spark-connect}"
SPARK_IMAGE="${SPARK_IMAGE:-lakebridge-spark-connect}"
SPARK_CONNECT_PORT="${SPARK_CONNECT_PORT:-15002}"
DOCKER_NETWORK="${DOCKER_NETWORK:-lakebridge}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dependencies
command -v docker >/dev/null || { echo "Docker not installed" >&2; exit 2; }

# Shared network for inter-container communication
docker network inspect "${DOCKER_NETWORK}" >/dev/null 2>&1 || docker network create "${DOCKER_NETWORK}"

# Build image if not present
if ! docker image inspect "${SPARK_IMAGE}" >/dev/null 2>&1; then
  echo "Building Spark Connect image..."
  docker build -t "${SPARK_IMAGE}" -f "${SCRIPT_DIR}/spark-connect.Dockerfile" "${SCRIPT_DIR}"
fi

# Start container if needed
if docker ps --format '{{.Names}}' | grep -qx "${SPARK_CONTAINER}"; then
  :
elif docker ps -a --format '{{.Names}}' | grep -qx "${SPARK_CONTAINER}"; then
  docker start "${SPARK_CONTAINER}" >/dev/null
else
  docker run --name "${SPARK_CONTAINER}" \
    --network "${DOCKER_NETWORK}" \
    -p "${SPARK_CONNECT_PORT}:15002" \
    -p 4040:4040 \
    -d "${SPARK_IMAGE}" >/dev/null
  echo "Starting Spark Connect container..."
fi

echo "Waiting up to 5 minutes for Spark Connect to be ready..."
MAX_WAIT=300; WAIT_INTERVAL=5; waited=0
while :; do
  if nc -z localhost "${SPARK_CONNECT_PORT}" 2>/dev/null; then
    break
  fi
  echo "waited=${waited}s"
  (( waited >= MAX_WAIT )) && { echo "ERROR: Spark Connect not ready in 300s" >&2; exit 1; }
  sleep "$WAIT_INTERVAL"; waited=$((waited + WAIT_INTERVAL))
done
echo "Spark Connect is fully started."
