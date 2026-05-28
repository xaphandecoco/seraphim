#!/bin/bash
set -e

TAG=${1:-stable}
REPO_DIR="/mnt/user/appdata/seraphim/repo"
COMPOSE_FILE="$REPO_DIR/docker-compose.unraid.yml"

cd "$REPO_DIR"

echo "Rolling back to tag: $TAG"

docker compose -f "$COMPOSE_FILE" down

docker tag "seraphim-backend:$TAG" seraphim-backend:unstable
docker tag "seraphim-frontend:$TAG" seraphim-frontend:unstable

docker compose -f "$COMPOSE_FILE" up -d

echo "Rollback to $TAG completed"
