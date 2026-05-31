#!/bin/bash
set -e

echo "Running database migrations..."
# Check if alembic_version table exists and has a version
if psql "$DATABASE_URL" -c "SELECT version_num FROM alembic_version LIMIT 1;" >/dev/null 2>&1; then
    echo "Database already initialized, stamping current version..."
    alembic stamp head || true
else
    alembic upgrade head
fi

echo "Starting application..."
exec "$@"
