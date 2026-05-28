#!/bin/bash
set -e

BACKUP_DIR="/mnt/user/backups/seraphim"
mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/db-$DATE.sql.gz"

echo "Starting PostgreSQL backup..."
docker exec seraphim-postgres pg_dump -U seraphim seraphim_attendance | gzip > "$BACKUP_FILE"

# Retention: keep 14 days of daily backups
find "$BACKUP_DIR" -name "db-*.sql.gz" -mtime +14 -delete

echo "Backup completed: $BACKUP_FILE"
