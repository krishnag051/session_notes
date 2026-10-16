#!/bin/sh
# Higher-stakes than deploy-staging.sh: this one touches the real Docker
# Hub repo and the real production stack. Same lesson learned from the
# staging project-name mixup applies here too -- an EXPLICIT Compose
# project name is used throughout, never left to directory-name
# inference (README.md's own documented production steps omit -p
# entirely, which is exactly the class of bug that cost two rounds to
# fix for staging -- not repeating that here).
#
# Confirmed directly (2026-10-15, not assumed): production and staging
# share this one host's ports (8006 backend, 3006 frontend -- see both
# compose files' own comments) and genuinely cannot run at the same
# time. This script checks whether staging is actually running right now
# and stops it first if so -- it does not guess.
#
# Usage:
#   ./deploy-production.sh
# Run from the repo root. Requires `docker login` already done for the
# krishna0051 Docker Hub account, and a real .env file sitting next to
# docker-compose.yml with the real production secrets (see README.md).
set -e

COMPOSE_FILE="docker-compose.yml"
PROJECT_NAME="session-notes-production"
BACKEND_IMAGE="krishna0051/session-notes-backend:latest"
FRONTEND_IMAGE="krishna0051/session-notes-frontend:latest"
PROD_API_BASE_URL="https://sn.masterfaster.org/api"

echo "Building backend image ($BACKEND_IMAGE)..."
docker build -f backend/Dockerfile -t "$BACKEND_IMAGE" .

echo "Building frontend image ($FRONTEND_IMAGE, API base: $PROD_API_BASE_URL)..."
docker build --build-arg VITE_API_BASE_URL="$PROD_API_BASE_URL" -f frontend/Dockerfile -t "$FRONTEND_IMAGE" .

echo "Pushing both images to Docker Hub..."
docker push "$BACKEND_IMAGE"
docker push "$FRONTEND_IMAGE"

echo "Checking whether staging is currently running (shares this host's ports)..."
if [ -n "$(docker compose -f docker-compose.staging.yml -p session-notes-staging ps -q 2>/dev/null)" ]; then
  echo "Staging is running -- stopping it first (production and staging cannot run at once on this host)."
  docker compose -f docker-compose.staging.yml -p session-notes-staging down
else
  echo "Staging is not running -- nothing to stop."
fi

echo "Bringing up production (project: $PROJECT_NAME)..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d

echo
echo "Real health check (retries for up to 15s while containers finish starting):"
healthy=0
for i in 1 2 3 4 5; do
  if curl -sf http://localhost:8006/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 3
done
if [ "$healthy" = "1" ]; then
  curl -s http://localhost:8006/health && echo " <- backend healthy"
else
  echo "backend health check FAILED -- check: docker logs ${PROJECT_NAME}-backend-1"
  exit 1
fi

echo
echo "Confirming migrations are at head..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend alembic current
