#!/bin/sh
# Real incident (2026-10-15), happened TWICE: a staging redeploy was run
# with the wrong Compose project name (-p reviewer-staging instead of
# -p session-notes-staging) -- Docker silently created a brand-new,
# disconnected stack reusing this repo's own docker-compose.staging.yml,
# the backend couldn't resolve its own "postgres" hostname, and the real
# session-notes-staging stack (and its real data) was left stopped and
# unreachable. Both times, the fix was the same: stop the wrongly-named
# containers, redeploy under the correct project name.
#
# This script exists so that fix is never needed again -- the project
# name and compose file are hardcoded here, not something to type or
# remember correctly every time. Run this from the repo root:
#
#   ./deploy-staging.sh
#
# Does exactly what README.md's own documented staging rebuild steps do,
# just without any flag left to get wrong.
set -e

COMPOSE_FILE="docker-compose.staging.yml"
PROJECT_NAME="session-notes-staging"

echo "Building backend + frontend (project: $PROJECT_NAME)..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build backend frontend

echo "Recreating backend + frontend..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d --force-recreate backend frontend

echo
echo "Done. Real health check (retries for up to 15s while the container finishes starting):"
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
fi
