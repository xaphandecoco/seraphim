#!/bin/bash
set -e

echo "Running database migrations..."
# Always upgrade — alembic skips already-applied revisions, so this is idempotent.
# The old 'stamp head' branch was wrong: it prevented new migrations from running on upgrade deploys.
alembic upgrade head

echo "Starting application..."
exec "$@"
