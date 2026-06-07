#!/bin/bash
set -e

BACKUP_DIR="/mnt/user/backups/seraphim"
STORAGE_DIR="/mnt/user/appdata/seraphim/storage"
mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y%m%d_%H%M%S)

echo "=== Seraphim backup started at $(date) ==="

# 1. PostgreSQL database dump
DB_FILE="$BACKUP_DIR/db-$DATE.sql.gz"
echo "Backing up PostgreSQL..."
docker compose -f docker-compose.unraid.yml exec -T postgres pg_dump -U seraphim seraphim_attendance | gzip > "$DB_FILE"
echo "  Database: $DB_FILE"

# 2. Face image storage (enrolled + detection snapshots)
STORAGE_FILE="$BACKUP_DIR/storage-$DATE.tar.gz"
if [ -d "$STORAGE_DIR" ]; then
    echo "Backing up storage volume..."
    tar -czf "$STORAGE_FILE" -C "$(dirname "$STORAGE_DIR")" "$(basename "$STORAGE_DIR")"
    echo "  Storage: $STORAGE_FILE"
else
    echo "  WARNING: Storage directory $STORAGE_DIR not found — skipping"
fi

# 3. Config volume (bootstrap.json etc.)
CONFIG_DIR="/mnt/user/appdata/seraphim/config"
CONFIG_FILE="$BACKUP_DIR/config-$DATE.tar.gz"
if [ -d "$CONFIG_DIR" ]; then
    tar -czf "$CONFIG_FILE" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
    echo "  Config: $CONFIG_FILE"
fi

# Retention: keep 14 days of daily backups
find "$BACKUP_DIR" -name "db-*.sql.gz" -mtime +14 -delete
find "$BACKUP_DIR" -name "storage-*.tar.gz" -mtime +14 -delete
find "$BACKUP_DIR" -name "config-*.tar.gz" -mtime +14 -delete

echo "=== Backup completed at $(date) ==="
echo ""
echo "To restore:"
echo "  DB:      zcat $DB_FILE | docker compose -f docker-compose.unraid.yml exec -T postgres psql -U seraphim seraphim_attendance"
echo "  Storage: tar -xzf $STORAGE_FILE -C /mnt/user/appdata/seraphim/"
echo "  Config:  tar -xzf $CONFIG_FILE -C /mnt/user/appdata/seraphim/"
echo "  Then:    docker compose exec seraphim-backend alembic upgrade head"
echo ""
echo "  ⚠ CAUTION: 'alembic upgrade head' must ONLY be run when restoring to the"
echo "    CURRENT (unrolled-back) image. If you rolled back the code, do NOT run"
echo "    this — a rolled-back image may not support the latest migration head."
echo "    See PRODUCTION_RUNBOOK.md §6.2 for rollback-aware restore procedures."
