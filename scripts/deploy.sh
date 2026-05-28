#!/bin/bash
set -e

REPO_DIR="/mnt/user/appdata/seraphim/repo"
COMPOSE_FILE="$REPO_DIR/docker-compose.unraid.yml"
LOG_FILE="/mnt/user/appdata/seraphim/logs/deploy.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec >> "$LOG_FILE" 2>&1

echo "=== Deploy started at $(date) ==="

cd "$REPO_DIR"

# Pull latest code
git pull origin main

# Get short SHA for tagging
SHA=$(git rev-parse --short HEAD)

# Build images
docker compose -f "$COMPOSE_FILE" build

# Tag with SHA for rollback
docker tag seraphim-backend:latest "seraphim-backend:$SHA"
docker tag seraphim-frontend:latest "seraphim-frontend:$SHA"

# Tag as unstable (current)
docker tag seraphim-backend:latest seraphim-backend:unstable
docker tag seraphim-frontend:latest seraphim-frontend:unstable

# Deploy
docker compose -f "$COMPOSE_FILE" up -d

# Cleanup old images (keep last 7 days)
docker image prune -f --filter "until=168h"

echo "=== Deploy completed at $(date) ==="
