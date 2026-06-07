#!/bin/bash
set -e

TAG=${1:-stable}
REPO_DIR="/mnt/user/appdata/seraphim/repo"
COMPOSE_FILE="$REPO_DIR/docker-compose.unraid.yml"
CLOUDFLARED_OVERLAY="$REPO_DIR/docker-compose.cloudflared.yml"

cd "$REPO_DIR"

echo "Rolling back to tag: $TAG"

# Pre-flight: confirm the target images exist locally BEFORE touching the running
# stack. Without this, a missing tag makes `docker tag` fail under `set -e` *after*
# the stack is already down, leaving no services running and no rollback completed.
for img in "seraphim-backend:$TAG" "seraphim-frontend:$TAG"; do
  if ! docker image inspect "$img" > /dev/null 2>&1; then
    echo "ERROR: image '$img' not found locally. Aborting; running stack left untouched." >&2
    exit 1
  fi
done

# Include the cloudflared overlay (when configured) so the public HTTPS tunnel is torn
# down and brought back up with the rest of the stack. Without it the tunnel service
# stays down after every rollback, killing public access.
COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ] && [ -f "$CLOUDFLARED_OVERLAY" ]; then
  COMPOSE_ARGS+=(-f "$CLOUDFLARED_OVERLAY")
fi

docker compose "${COMPOSE_ARGS[@]}" down

docker tag "seraphim-backend:$TAG" seraphim-backend:unstable
docker tag "seraphim-frontend:$TAG" seraphim-frontend:unstable

docker compose "${COMPOSE_ARGS[@]}" up -d

echo "Rollback to $TAG completed"
