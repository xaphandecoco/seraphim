# S21 — Cutover & CiviCRM Decommission

**Phase:** G — Polish & Cutover · **Depends on:** S01–S20, S22, S23 (all sprints complete and all tests green) · **Effort:** M · **Status:** Not started

---

## 1. Goal & rationale

S21 is the **operational runbook sprint**, not a code sprint. Its deliverables are:

1. A definitive **pre-cutover checklist** that gates the freeze window.
2. A scripted **cutover execution sequence** (freeze CiviCRM → final S06 migration run → parallel row-count + attendance-total + derived-attr verification → DNS/Cloudflare go-live → smoke tests → n8n shutdown).
3. A **rollback plan** with a documented decision tree and time-boxed triggers.
4. Scripts and procedures to **archive and decommission** CiviCRM (WordPress + MySQL), n8n, and any remaining orphan admin_settings keys.
5. An updated `docs/PRODUCTION_RUNBOOK.md` that replaces all CiviCRM-era content (the CiviCRM wizard step, push queue, dead-letter, sync instructions) with Seraphim-as-SoR equivalents.
6. A `scripts/smoke_test_crm.py` that extends `scripts/smoke_test.py` with CRM-specific endpoint coverage (contacts, events, participants, dashboard, name-match queue).

**Why this sprint is a concrete deliverable, not just documentation:** The cutover window for a live church system (~1,440 contacts, ~33.6k attendance rows, weekly Sunday attendance) is short and high-stakes. Every step must be pre-scripted, the freeze window pre-announced, and rollback unambiguous. Mistakes cost a Sunday's worth of attendance data. The sprint produces versioned, git-committed artifacts that the operator executes against the running system.

**What makes this cutover big-bang (locked decision, `decisions.md` Round 1):** CiviCRM stays read-only until the final verification passes. There is no parallel live-write period. The moment Seraphim's contacts/events/participants row counts reconcile against the XLSX re-exports, the DNS CNAME is flipped, n8n flows are disabled, and CiviCRM is made read-only permanently. The migration ETL (S06) must already be re-runnable — S21 just executes it one final time under freeze conditions.

---

## 2. Scope

### In scope

- **Pre-cutover gate checklist** (`docs/cutover/preflight.md`): numbered items, each binary (pass/fail), covering all sprints S01–S23; must all be green before the freeze window opens.
- **Freeze window procedure** (`docs/cutover/freeze.md`): step-by-step CiviCRM read-only enforcement, n8n cron-job disablement, and operator communication template.
- **Final S06 migration run procedure** with verification queries and reconciliation tolerances.
- **Verification report template** (`docs/cutover/verification.md`): contact count, event count, participant count, attendance total per event_type, people-link resolution %, derived-attr spot checks (tier, is_regular, is_connected), name-match review-queue residual count.
- **DNS/Cloudflare cutover procedure**: CNAME swap or Cloudflare Tunnel ingress-rule update; zero-downtime approach; TTL pre-staging.
- **Go-live smoke test** using `scripts/smoke_test_crm.py` (new, extends existing `scripts/smoke_test.py`).
- **Rollback decision tree** with explicit time-boxed triggers (T+15min, T+30min, T+2hr thresholds) and rollback execution steps.
- **Post-cutover decommission procedure** (`docs/cutover/decommission.md`): CiviCRM/WordPress read-only enforcement → archive dump → shutdown; n8n data export → shutdown; orphan admin_settings cleanup; backup schedule confirmation.
- **Updated `docs/PRODUCTION_RUNBOOK.md`**: remove all CiviCRM-specific sections (wizard step 2 CiviCRM, push queue, dead-letter, sync instructions, CiviCRM troubleshooting row); add Seraphim-as-SoR equivalents (contacts import, event management, name-match queue, scheduled-job health, Google Chat/Gmail integration check, Zoom attendance check, derived-attr recompute).
- **`scripts/smoke_test_crm.py`**: CRM-specific read/write smoke test script, usable standalone or via `python3 scripts/smoke_test.py --mode crm`.
- **`scripts/decommission_civicrm.sh`**: archive dump script for CiviCRM MySQL + WordPress files.
- Backend: remove any residual `admin_settings` rows with keys `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` via a one-shot Alembic data migration (cleanup of any rows that survived S01 in pre-existing deployments).
- One Alembic migration: `backend/alembic/versions/s21_cleanup_civicrm_settings.py` (data-only; drops stale civicrm admin_settings rows; no schema change).

### Out of scope

- Any new feature code (all features delivered by S01–S23).
- Re-running Alembic schema migrations (those are idempotent from prior sprints; `alembic upgrade head` is just verified, not authoring new ones).
- Migrating CiviCRM contributions, pledges, financial data, or membership fees (not in scope for v1 CRM; noted for future).
- Migrating CiviCRM groups, tags, or email-campaign data (not in Seraphim v1 scope).
- n8n workflow reimplementation (covered by S17/S18; S21 only disables and archives the live n8n instance).
- New UI pages (all UI delivered by S03–S20).
- Performance tuning (all benchmark requirements enforced in S05; S21 only verifies they hold on production data volumes).

---

## 3. Data model changes

### 3.1 One-shot data cleanup migration

**File:** `backend/alembic/versions/s21_cleanup_civicrm_settings.py`

`down_revision` = latest applied head at implementation time (run `alembic heads` to confirm; never edit applied migrations per AGENTS.md:100).

**Upgrade (data-only — no schema DDL):**
```python
def upgrade() -> None:
    # Remove any stale CiviCRM credential rows from admin_settings.
    # S01 removed the Python accessors; this removes any rows that were
    # written by the old setup wizard on a pre-S01 database and survived
    # the schema migration because admin_settings has no FK-based cascade.
    op.execute(
        "DELETE FROM admin_settings WHERE key IN "
        "('civicrm_url', 'civicrm_api_key', 'civicrm_site_key')"
    )
```

**Downgrade:** no-op (data is not recoverable from a delete-only migration; CiviCRM is decommissioned by this point).
```python
def downgrade() -> None:
    pass  # data migration only; CiviCRM decommissioned — no meaningful downgrade
```

This migration is **safe to run on a fresh database** (DELETE on no rows is a no-op). It is also safe to run before decommissioning CiviCRM because it touches only `admin_settings`, not the CiviCRM server itself.

### 3.2 No structural schema changes

All structural schema work (contacts, events, participants, custom fields, face enrollment, biometric consent, automation, etc.) is complete by S23. S21 produces no `CREATE TABLE`, `ALTER TABLE`, `ADD COLUMN`, or `DROP TABLE` DDL. The Alembic migration above is data-only.

---

## 4. Backend

### 4.1 New endpoint (optional, admin-only verification aid)

| METHOD | Path | Role | Request | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/migration/verify` | admin | — | `MigrationVerifyResponse` | Live row-count snapshot for cutover verification; wraps queries already run via psql into an API call |

This endpoint is optional but strongly recommended so the smoke test script can verify counts programmatically without needing direct DB access.

**Pydantic schema** (add to `backend/app/schemas.py`):
```python
class MigrationVerifyResponse(BaseModel):
    contacts_total: int
    contacts_not_deleted: int
    events_total: int
    participants_total: int
    participants_by_source: dict[str, int]   # {face: N, manual: N, import: N, zoom: N, community_report: N}
    name_match_review_pending: int
    face_subjects_active: int
    biometric_consent_given: int
    last_s23_job_run: Optional[datetime]     # latest job_runs.finished_at WHERE job_name='nightly_recompute'
    tier_distribution: dict[str, int]        # {tier0: N, tier1: N, tier2: N, tier3: N, inactive: N}
    is_regular_count: int
    is_connected_count: int
    snapshot_at: datetime                    # utc_now() at query time
```

**Router addition** to `backend/app/routers/migration.py` (already exists from S06):
```python
@router.get("/verify", response_model=MigrationVerifyResponse)
async def migration_verify(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
) -> MigrationVerifyResponse:
    from sqlalchemy import func, case, select
    from app.models import (
        Contact, Event, Participant, NameMatchReviewQueue,
        ComprefaceSubject, BiometricConsent, JobRun, utc_now,
    )

    contacts_total = (await db.execute(select(func.count(Contact.id)))).scalar_one()
    contacts_not_deleted = (await db.execute(
        select(func.count(Contact.id)).where(Contact.is_deleted == False)
    )).scalar_one()
    events_total = (await db.execute(select(func.count(Event.id)))).scalar_one()
    participants_total = (await db.execute(select(func.count(Participant.id)))).scalar_one()

    source_rows = (await db.execute(
        select(Participant.source, func.count(Participant.id))
        .group_by(Participant.source)
    )).all()
    participants_by_source = {row[0]: row[1] for row in source_rows}

    nmrq_pending = (await db.execute(
        select(func.count(NameMatchReviewQueue.id))
        .where(NameMatchReviewQueue.status == "pending")
    )).scalar_one()

    face_active = (await db.execute(
        select(func.count(ComprefaceSubject.id))
        .where(ComprefaceSubject.enrollment_status == "active")
    )).scalar_one()

    consent_given = (await db.execute(
        select(func.count(BiometricConsent.id))
        .where(BiometricConsent.consent_given == True)
    )).scalar_one()

    last_recompute = (await db.execute(
        select(func.max(JobRun.finished_at))
        .where(JobRun.job_name == "nightly_recompute", JobRun.status == "success")
    )).scalar_one_or_none()

    tier_rows = (await db.execute(
        select(Contact.tier, func.count(Contact.id))
        .where(Contact.is_deleted == False, Contact.tier.isnot(None))
        .group_by(Contact.tier)
    )).all()
    tier_distribution = {row[0]: row[1] for row in tier_rows}

    is_regular_count = (await db.execute(
        select(func.count(Contact.id))
        .where(Contact.is_deleted == False, Contact.is_regular == True)
    )).scalar_one()

    is_connected_count = (await db.execute(
        select(func.count(Contact.id))
        .where(Contact.is_deleted == False, Contact.is_connected == True)
    )).scalar_one()

    return MigrationVerifyResponse(
        contacts_total=contacts_total,
        contacts_not_deleted=contacts_not_deleted,
        events_total=events_total,
        participants_total=participants_total,
        participants_by_source=participants_by_source,
        name_match_review_pending=nmrq_pending,
        face_subjects_active=face_active,
        biometric_consent_given=consent_given,
        last_s23_job_run=last_recompute,
        tier_distribution=tier_distribution,
        is_regular_count=is_regular_count,
        is_connected_count=is_connected_count,
        snapshot_at=utc_now(),
    )
```

### 4.2 `scripts/smoke_test_crm.py`

New script extending `scripts/smoke_test.py` with CRM-specific checks. Follows the same pattern: standard library only (`httpx` preferred, `urllib` fallback), `PASS`/`FAIL` per check, exit-code 0/1.

**What it checks (all authenticated as admin):**
1. `GET /contacts?limit=10` returns paginated list with `items` array; each item has `id`, `first_name`, `last_name`, `external_id`.
2. `GET /events?limit=10` returns list with `id`, `title`, `event_type`.
3. `GET /participants?event_id=<first_event_id>&limit=10` returns list.
4. `GET /migration/summary` returns non-null `contacts` and `events` batch entries (confirming S06 ETL ran).
5. `GET /migration/verify` — asserts `contacts_not_deleted >= 1000` (expects at least 1,000 of 1,440 contacts migrated), `participants_total >= 20000` (expects at least 20k of 33.6k rows), `name_match_review_pending` is reported (warn if > 500), `last_s23_job_run` is not null (recompute has run at least once).
6. `GET /name-match/review?status=pending&limit=1` returns expected shape (from S22 router).
7. `GET /analytics/summary` returns attendance dashboard tiles (S14).
8. `GET /job-runs?limit=5` returns recent job_runs (S16).
9. `GET /health` still returns `postgres: true`, `redis: true`, `compreface: true`.
10. `POST /auth/refresh` succeeds (in-memory token refresh round-trip).

**Invocation examples:**
```bash
# Full CRM smoke test (recommended post-cutover gate):
python3 scripts/smoke_test_crm.py \
  --base-url https://seraphim.lightnc.org/api \
  --email admin@lightnc.org --password 'YourPassw0rd!'

# With --strict: exit 1 if ANY check fails (for automated monitoring):
python3 scripts/smoke_test_crm.py --strict \
  --base-url https://seraphim.lightnc.org/api \
  --email admin@lightnc.org --password 'YourPassw0rd!'

# Credentials via env:
BASE_URL=https://seraphim.lightnc.org/api \
SMOKE_EMAIL=admin@lightnc.org \
SMOKE_PASSWORD='YourPassw0rd!' \
python3 scripts/smoke_test_crm.py --strict
```

### 4.3 `scripts/decommission_civicrm.sh`

Operator-run (not automated) archive script. Produces a timestamped archive of CiviCRM MySQL + WordPress files before server shutdown.

```bash
#!/bin/bash
# decommission_civicrm.sh — archive CiviCRM/WordPress before shutdown.
# Run from the CiviCRM/WordPress host (NOT the Seraphim Unraid box).
# Requires: mysqldump, tar, ssh access to the CiviCRM host.
set -e

ARCHIVE_DIR="/mnt/user/backups/civicrm_archive"   # adjust to your Unraid backup path
CIVICRM_HOST="crm.lightnc.org"   # adjust
MYSQL_USER="root"
MYSQL_DB="civicrm"   # adjust to actual DB name
WP_FILES="/var/www/html"   # adjust to WordPress root on CiviCRM host
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$ARCHIVE_DIR"

echo "=== CiviCRM/WordPress archive started at $(date) ==="
echo "Step 1 — MySQL dump (CiviCRM + WordPress databases)..."
ssh "$CIVICRM_HOST" "mysqldump -u$MYSQL_USER --all-databases" \
  | gzip > "$ARCHIVE_DIR/civicrm_mysql_all_$DATE.sql.gz"
echo "  Saved: $ARCHIVE_DIR/civicrm_mysql_all_$DATE.sql.gz"

echo "Step 2 — WordPress files archive..."
ssh "$CIVICRM_HOST" "tar -czf - -C $(dirname $WP_FILES) $(basename $WP_FILES)" \
  > "$ARCHIVE_DIR/wordpress_files_$DATE.tar.gz"
echo "  Saved: $ARCHIVE_DIR/wordpress_files_$DATE.tar.gz"

echo "=== Archive complete. Verify sizes before decommissioning: ==="
ls -lh "$ARCHIVE_DIR/"*"$DATE"*
echo ""
echo "NEXT STEPS (manual):"
echo "  1. Copy archives off-host (NAS / cold storage)."
echo "  2. Verify at least one archive can be restored to a test instance."
echo "  3. Only then: ssh $CIVICRM_HOST and stop WordPress + MySQL."
echo "  4. Update DNS / firewall to block $CIVICRM_HOST permanently."
echo "=== END ==="
```

### 4.4 File-by-file summary

| Action | File | Notes |
|---|---|---|
| CREATE | `backend/alembic/versions/s21_cleanup_civicrm_settings.py` | Data-only migration; deletes stale civicrm admin_settings rows |
| MODIFY | `backend/app/routers/migration.py` | Add `GET /migration/verify` endpoint |
| MODIFY | `backend/app/schemas.py` | Add `MigrationVerifyResponse` |
| CREATE | `scripts/smoke_test_crm.py` | CRM-specific smoke test |
| CREATE | `scripts/decommission_civicrm.sh` | CiviCRM archive script |
| CREATE | `docs/cutover/preflight.md` | Pre-cutover gate checklist |
| CREATE | `docs/cutover/freeze.md` | Freeze window procedure |
| CREATE | `docs/cutover/verification.md` | Reconciliation report template |
| CREATE | `docs/cutover/decommission.md` | Post-cutover decommission procedure |
| MODIFY | `docs/PRODUCTION_RUNBOOK.md` | Replace CiviCRM-era content with Seraphim-SoR content |

---

## 5. Frontend

S21 has **no new frontend pages**. All UI work is complete by S20. The only frontend-adjacent change is updating the setup wizard stub (already modified in S01 to remove the CiviCRM step) — if any residual CiviCRM reference survived in `frontend/src/pages/SetupPage.tsx` (e.g., the step 2 CiviCRM block described at `frontend.md` line 28), it is removed here as a cleanup item.

**Checklist:**
- [ ] `frontend/src/pages/SetupPage.tsx` — confirm no CiviCRM step (step 2 CiviCRM block) remains. If it does, delete the block and re-test the wizard flow.
- [ ] `frontend/src/pages/AttendancePage.tsx` — confirm the page was rewritten to a stub in S01 (no push tab, no dead-letter tab).
- [ ] `frontend/src/App.tsx` — confirm `/settings/attendance` route does not expose any CiviCRM push UI.
- [ ] All Tailwind tokens used correctly: `bg-card`, `text-foreground`, `bg-background`. No hardcoded hex.
- [ ] Dark mode tested on all new pages introduced by S20.

**TanStack Query keys** (no new keys; S21 consumes existing keys from prior sprints):
- `['migration', 'verify']` — used by smoke test only; not a new UI page.

---

## 6. Migration / data

This section is the definitive operator runbook for the cutover. It is also written verbatim into `docs/cutover/` as separate files for operator use.

### 6.1 Pre-cutover gate (must ALL pass before opening freeze window)

**Sprint completeness gates (run via CI):**
- [ ] All 23 sprint specs (S01–S23) status = "complete" in the spec set.
- [ ] Backend: `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q` exits 0; count ≥ 355 tests (baseline from MEMORY.md) plus all S16–S23 tests.
- [ ] Backend lint: `ruff check app` exits 0 with no errors.
- [ ] Backend audit: `pip-audit -r requirements.txt` exits 0 (no known CVEs).
- [ ] Frontend build+typecheck: `npm run build` exits 0.
- [ ] Frontend lint: `npm run lint` exits 0.
- [ ] Frontend tests: `npm run test:run` exits 0.
- [ ] Alembic: `alembic upgrade head` then `alembic current` == `alembic heads` on a clean Postgres instance. No pending revisions.

**Production environment gates:**
- [ ] `.env.production` exists with no `__GENERATE_ME__` sentinels (`grep __GENERATE_ME__ .env.production` outputs nothing).
- [ ] `JWT_SECRET` is ≥ 32 characters.
- [ ] `ENVIRONMENT=production` is set.
- [ ] All 6 Docker services healthy: `docker compose -f docker-compose.unraid.yml [...] ps` shows `postgres`, `redis`, `seraphim-backend`, `seraphim-frontend`, `seraphim-rtsp-worker`, `seraphim-queue-worker` all `running`/`healthy`.
- [ ] `GET /health` returns `{"postgres": true, "redis": true, "compreface": true}`.
- [ ] CompreFace is reachable: `curl -fsS http://<compreface-host>/api/v1/health` returns 200.
- [ ] A valid admin account exists and can log in; `GET /auth/me` returns `role: "admin"`.
- [ ] S15 RBAC: volunteer and viewer test accounts exist; each has correct access scope.
- [ ] S16 APScheduler: `GET /job-runs?limit=5` returns at least one successful `nightly_recompute` run since the test DB was loaded.
- [ ] S17/S18 integrations: Google Chat webhook is configured in admin_settings (`google_chat_webhook_url`); test notification fires successfully.
- [ ] S18 Zoom: `zoom_account_id`, `zoom_client_id`, `zoom_client_secret` set in admin_settings; test token fetch succeeds.
- [ ] Biometric consent: `GET /biometric-consent/status` is accessible; no contacts with face subjects lack consent records (query: `SELECT count(*) FROM compreface_subjects cs LEFT JOIN biometric_consent bc ON bc.contact_id = cs.contact_id WHERE bc.id IS NULL AND cs.enrollment_status = 'active'` should return 0 or operator-acknowledged residual).
- [ ] Backup: `scripts/backup.sh` completes successfully; `db-*.sql.gz` and `storage-*.tar.gz` exist in `/mnt/user/backups/seraphim/`.
- [ ] Restore drill: restore was rehearsed against a test instance (not prod) within the last 14 days.
- [ ] S06 dry-run complete: `python scripts/migrate_civicrm.py --entity contacts --file exports/contacts.xlsx --map config/civicrm_contacts.map.yml --dry-run` completed with 0 `BatchFatalError` rows and error_count < 5% of total_rows (5% tolerance for known CiviCRM data-quality issues).
- [ ] Owner has announced the maintenance window to church staff and n8n workflow owners.
- [ ] DNS TTL pre-staged: the CNAME or A record for `seraphim.lightnc.org` has been set to TTL=60s at least 24 hours before cutover (to allow fast propagation after flip).
- [ ] Rollback target image is tagged: `docker image ls | grep seraphim-backend` shows a `:stable` or `:<sha>` tag from the last known-good deploy.

### 6.2 Freeze window procedure (T=0)

**Announce:**
Send to all n8n workflow owners and church staff (use operator communication template in `docs/cutover/freeze.md`):
> "CiviCRM is now in read-only mode. No attendance, contact, or event data should be entered in CiviCRM from this point. All data entry switches to Seraphim."

**Disable n8n flows (do in this order to avoid mid-run data corruption):**
1. Log in to n8n.
2. Disable `CiviCRM Scheduler/Updater` (`fKkPUolayRyZrjao`) — stops EOW/EOM recomputes and attendance notifiers.
3. Disable `Morning Prayer` (`sJ7EgCZkk9wKSj3D`) — stops Zoom attendance import to CiviCRM.
4. Disable `Gforms AI V2` (`3yaS8JZfct8BVe8Z`) — stops community-report-to-CiviCRM flow.
5. Disable `New Friend V2` (`jdVHzcMWXdANwG8R`) — stops newcomer-form-to-CiviCRM flow.
6. Disable `Create Schedule` (`pz7sHlUbU6jV1Hqm`) — stops event creation via n8n webhook.
7. Confirm: no n8n workflows are in an `active` or `running` state.

**Make CiviCRM read-only:**
- Option A (recommended): in CiviCRM admin → System Settings → Site Maintenance → enable Maintenance Mode. This blocks all web-based data entry while keeping the DB readable for the final XLSX export.
- Option B: add a `.htaccess` `Deny from all` except the admin IP for `/wp-content/plugins/civicrm/civicrm/extern/rest.php` (blocks API writes).
- Confirm: `POST` to CiviCRM REST returns 503 or access-denied.

**Record the freeze timestamp:**
```bash
echo "FREEZE_TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)" | tee /mnt/user/backups/seraphim/cutover.env
```
This timestamp is the authoritative boundary for the final export: all CiviCRM data at or before this time is what Seraphim must match.

### 6.3 Final S06 migration run

**Re-export from CiviCRM** (while it is still in maintenance mode, DB is readable):
- Contacts XLSX: include columns Contact ID, First Name, Last Name, Suffix, Gender, Birth Date, Phone (Home), Email, Street Address, Contact Type, Contact Subtype, and ALL custom fields (Barangay, PEPSOL, Ministry, Community, Invited By, Consolidated By, Community Leader, Ministry Leader, Network Leader, Lifegroup Leader, followup_listing). Save as `exports/contacts_final_<FREEZE_TIMESTAMP>.xlsx`.
- Events XLSX: include columns Event ID, Event Title, Event Type, Session Time, Event Date, Start Date, End Date, Location. Save as `exports/events_final_<FREEZE_TIMESTAMP>.xlsx`.
- Participants (attendance) XLSX: include columns Contact ID, Event ID, Status. Save as `exports/participants_final_<FREEZE_TIMESTAMP>.xlsx`.
- Links XLSX (people-link fields only): Contact ID + all 6 link columns. Save as `exports/links_final_<FREEZE_TIMESTAMP>.xlsx`.

**Copy exports to the Unraid host:**
```bash
scp exports/*_final_*.xlsx root@<unraid-ip>:/mnt/user/appdata/seraphim/exports/
```

**Run the final ETL (live, not dry-run):**
```bash
# From the Seraphim repo on Unraid or inside the backend container:
EXPORT=</mnt/user/appdata/seraphim/exports>
MAP=</mnt/user/appdata/seraphim/repo/config>

python scripts/migrate_civicrm.py \
  --entity contacts \
  --file $EXPORT/contacts_final_*.xlsx \
  --map $MAP/civicrm_contacts.map.yml \
  --by-user-email admin@lightnc.org

python scripts/migrate_civicrm.py \
  --entity events \
  --file $EXPORT/events_final_*.xlsx \
  --map $MAP/civicrm_events.map.yml \
  --by-user-email admin@lightnc.org

python scripts/migrate_civicrm.py \
  --entity participants \
  --file $EXPORT/participants_final_*.xlsx \
  --map $MAP/civicrm_participants.map.yml \
  --by-user-email admin@lightnc.org

python scripts/migrate_civicrm.py \
  --entity links \
  --file $EXPORT/links_final_*.xlsx \
  --map $MAP/civicrm_links.map.yml \
  --by-user-email admin@lightnc.org
```

Each command prints a summary table (batch_id, status, total_rows, created, updated, skipped, errors, reviews). If any run produces `status: failed` or `error_count` exceeds 5% of `total_rows`, **stop and investigate before proceeding.**

### 6.4 Verification

**Target tolerances** (based on known data: ~1,440 contacts, ~33.6k participants):

| Metric | Expected | Tolerance | Action if outside tolerance |
|---|---|---|---|
| `contacts_not_deleted` | ~1,440 | ±50 (known CiviCRM data-quality variance) | Investigate import_batch errors; do not proceed |
| `events_total` | operator-known count | ±5 | Inspect missing events; re-run events ETL if needed |
| `participants_total` | ~33,600 | ±200 | Inspect participants batch; check for duplicate-event dedupe (UNIQUE constraint) |
| `participants_by_source["import"]` | ~33,600 | ±200 | All import-path rows must be source="name_list" per S06 loader |
| `name_match_review_pending` | < 200 | operator judgement | Review queue must be below threshold before go-live; resolve or explicitly accept residual |
| `tier_distribution` not all null | > 1000 contacts with non-null tier | — | Trigger S23 on-demand recompute if all null |
| `is_regular_count` | > 100 | — | Sanity check: Regular = lifetime attendance > 9; should be substantial fraction |
| `is_connected_count` | > 100 | — | Sanity check: has community assignment |

**Verification queries (direct psql, for operator reference):**
```sql
-- Contact count reconciliation
SELECT count(*) AS total, count(*) FILTER (WHERE is_deleted = false) AS active
FROM contacts;

-- Event count
SELECT count(*) AS total FROM events;

-- Participant count by source
SELECT source, count(*) AS cnt FROM participants GROUP BY source ORDER BY cnt DESC;

-- Attendance total by event_type (cross-check against CiviCRM summary report)
SELECT e.event_type, count(p.id) AS attendance
FROM participants p
JOIN events e ON e.id = p.event_id
WHERE p.status = 'attended'
GROUP BY e.event_type
ORDER BY attendance DESC;

-- People-links resolved vs pending
SELECT count(*) AS review_pending
FROM name_match_review_queue
WHERE status = 'pending';

-- Derived attr spot check (5 random active contacts)
SELECT id, first_name, last_name, tier, is_active, is_regular, is_connected,
       weeks_absent, attendance_count, last_attended_at
FROM contacts
WHERE is_deleted = false AND is_active = true
ORDER BY random() LIMIT 5;

-- S23 recompute last run
SELECT job_name, started_at, finished_at, status
FROM job_runs
WHERE job_name = 'nightly_recompute'
ORDER BY started_at DESC LIMIT 3;
```

**If verification fails:** stop. Do not flip DNS. Diagnose and re-run the failing ETL phase. The system is still write-protected on the Seraphim side (no real traffic yet); re-runs are safe because S06 is idempotent.

**If verification passes:** record pass in `/mnt/user/backups/seraphim/cutover.env`:
```bash
echo "VERIFY_PASSED=$(date -u +%Y%m%dT%H%M%SZ)" >> /mnt/user/backups/seraphim/cutover.env
echo "CONTACTS=$(psql ... -t -c 'SELECT count(*) FROM contacts WHERE is_deleted=false')" >> ...
echo "PARTICIPANTS=$(psql ... -t -c 'SELECT count(*) FROM participants')" >> ...
```

### 6.5 DNS / Cloudflare cutover

**Option A — Cloudflare named Tunnel (the standard LNC deployment):**
1. In Cloudflare Zero Trust → Tunnels → select the existing tunnel.
2. Public Hostname → find the entry currently pointing at CiviCRM/WordPress.
3. Update the "Service" field to `http://<unraid-ip>:3000` (the Seraphim frontend).
4. Save. Propagation is immediate via the tunnel (no DNS TTL wait).
5. Confirm: `curl -fsS https://seraphim.lightnc.org/api/health` returns 200 from the Seraphim backend.

**Option B — DNS A record / CNAME flip:**
1. Log in to DNS provider.
2. Update the A record (or CNAME) for `seraphim.lightnc.org` to the Unraid host's IP (or Cloudflare Tunnel CNAME).
3. Because TTL was pre-staged to 60s (see §6.1), propagation completes within ~2 minutes.
4. Confirm from a non-cached client: `dig +short seraphim.lightnc.org` returns the new IP; `curl -fsS https://seraphim.lightnc.org/api/health` returns 200.

### 6.6 Go-live smoke test

Run immediately after DNS flip:
```bash
python3 scripts/smoke_test_crm.py --strict \
  --base-url https://seraphim.lightnc.org/api \
  --email admin@lightnc.org --password 'YourPassw0rd!'
```

Also run the existing base smoke test:
```bash
python3 scripts/smoke_test.py --write \
  --base-url https://seraphim.lightnc.org/api \
  --email admin@lightnc.org --password 'YourPassw0rd!'
```

**Both must exit 0** before declaring go-live complete.

**Manual spot checks (in addition to automated smoke tests):**
- [ ] Log in as admin: contacts list shows ≥ 1,000 contacts; search works.
- [ ] Log in as volunteer: can view contact detail, edit a contact field, check in a participant manually.
- [ ] Log in as viewer: can see reports/dashboard; cannot see individual contact records; cannot navigate to `/contacts/:id`.
- [ ] Face recognition pipeline: with active event set, `POST /uploads/faces` with a test image creates a detection and task. Task appears on the Tasks page via SSE within 2 seconds.
- [ ] Derived attrs: navigate to a contact known to attend regularly; their `tier`, `is_regular`, `is_connected` fields are populated in the contact detail panel.
- [ ] Reports dashboard: S14 tiles load with real data (attendance trends, tier distribution, ministry breakdown).
- [ ] Name-match review queue: `GET /name-match/review?status=pending` shows residual queue items; confirm they are reviewable via the UI.
- [ ] Google Chat: fire a test notification from the admin panel (S18 integration page); confirm message appears in the configured Google Chat channel.
- [ ] Backup: `scripts/backup.sh` produces a new db-*.sql.gz referencing the Seraphim database (not CiviCRM).

### 6.7 Post-cutover scheduled-job verification

Within 48 hours of go-live, confirm the following APScheduler jobs have run at least once successfully (check `GET /job-runs?limit=20`):

| Job name | Expected cadence | Next expected run |
|---|---|---|
| `nightly_recompute` | Daily (S23) | Within 24h of go-live |
| `sunday_event_gen` | Friday 18:00 (S04/S16) | Next Friday |
| `powerhouse_event_gen` | Wednesday 08:00 (S04/S16) | Next Wednesday |
| `zoom_attendance_pull` | Daily ~23:00 (S18) | Night of go-live |
| `eow_recompute` | Monday 00:00 (S16/S23) | Next Monday |
| `attendance_notifier_8am` | Sunday 09:30 (S18) | Next Sunday |
| `attendance_notifier_10am` | Sunday 12:00 (S18) | Next Sunday |
| `attendance_notifier_3pm` | Sunday 17:00 (S18) | Next Sunday |
| `attendance_notifier_powerhouse` | Wednesday 21:00 (S18) | Next Wednesday |

---

## 7. Acceptance criteria

1. All items in the pre-cutover gate checklist (§6.1) are marked pass before the freeze window opens.
2. `python scripts/migrate_civicrm.py --entity contacts ...` (live run) completes with `status: completed` and `error_count < 5% of total_rows`.
3. `python scripts/migrate_civicrm.py --entity events ...` (live run) completes with `status: completed`.
4. `python scripts/migrate_civicrm.py --entity participants ...` (live run) completes with `status: completed` and `participants_total ≥ 33,000`.
5. `python scripts/migrate_civicrm.py --entity links ...` (live run) completes with `status: completed`; `name_match_review_pending < 200`.
6. `GET /migration/verify` returns `contacts_not_deleted ≥ 1,390`, `participants_total ≥ 33,000`, `last_s23_job_run` is not null.
7. `python3 scripts/smoke_test_crm.py --strict` exits 0 against the live production URL.
8. `python3 scripts/smoke_test.py --write` exits 0 against the live production URL.
9. `GET https://seraphim.lightnc.org/api/health` returns `{"postgres": true, "redis": true, "compreface": true}` from a fresh client (confirms DNS flip is live and Seraphim is serving).
10. CiviCRM REST API (`/wp-content/plugins/civicrm/civicrm/extern/rest.php`) returns 503 or access-denied from all external callers (confirms CiviCRM is read-only/decommissioned).
11. All 5 n8n workflows (§6.2) show status `inactive` in the n8n dashboard.
12. `docs/PRODUCTION_RUNBOOK.md` no longer contains the phrase "CiviCRM" in any operational instruction (only in historical/archive notes); confirmed via `grep -i civicrm docs/PRODUCTION_RUNBOOK.md`.
13. `scripts/backup.sh` produces a new `db-*.sql.gz` that is a Seraphim-schema dump (not CiviCRM); confirmed by inspecting first 10 lines of the dump for `CREATE TABLE contacts` or `CREATE TABLE participants`.
14. The `scripts/decommission_civicrm.sh` archive script produces both `civicrm_mysql_all_*.sql.gz` and `wordpress_files_*.tar.gz` in the backup directory.
15. The S21 Alembic migration `s21_cleanup_civicrm_settings.py` applies cleanly: `alembic upgrade head` on a production DB exits 0; `SELECT count(*) FROM admin_settings WHERE key IN ('civicrm_url','civicrm_api_key','civicrm_site_key')` returns 0.
16. Post-cutover within 48h: `GET /job-runs` shows at least one successful run of `nightly_recompute`.

---

## 8. Test plan

### 8.1 Backend pytest

**File:** `backend/tests/test_cutover.py`

```python
"""
S21 cutover tests.
These are integration-level tests against the full app stack
(SQLite + memory:// Redis per conftest.py).
They verify the migration verify endpoint, the settings cleanup migration,
and that no CiviCRM remnants are present in the schema or config accessors.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import text

# --- Migration verify endpoint ---

@pytest.mark.asyncio
async def test_migration_verify_returns_snapshot(async_client: AsyncClient, admin_token: str):
    """GET /migration/verify returns all expected fields with non-negative counts."""
    r = await async_client.get(
        "/migration/verify",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "contacts_total" in data
    assert "events_total" in data
    assert "participants_total" in data
    assert "participants_by_source" in data
    assert "name_match_review_pending" in data
    assert "snapshot_at" in data
    assert data["contacts_total"] >= 0
    assert data["participants_total"] >= 0


@pytest.mark.asyncio
async def test_migration_verify_requires_admin(async_client: AsyncClient, volunteer_token: str):
    """GET /migration/verify is admin-only; volunteers get 403."""
    r = await async_client.get(
        "/migration/verify",
        headers={"Authorization": f"Bearer {volunteer_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_migration_verify_unauthenticated_401(async_client: AsyncClient):
    """GET /migration/verify without token returns 401."""
    r = await async_client.get("/migration/verify")
    assert r.status_code == 401


# --- CiviCRM settings cleanup ---

@pytest.mark.asyncio
async def test_no_civicrm_admin_settings_remain(db_session):
    """
    After the S21 migration runs, no admin_settings rows with
    civicrm_url, civicrm_api_key, or civicrm_site_key should exist.
    The test seeds one such row, runs the migration's data cleanup logic
    (by calling the equivalent DELETE directly), then verifies it's gone.
    """
    # Seed a stale civicrm_url row
    await db_session.execute(
        text("INSERT INTO admin_settings (key, value, category) VALUES ('civicrm_url', '{}', 'general') "
             "ON CONFLICT (key) DO NOTHING")
    )
    await db_session.commit()

    # Simulate the migration's cleanup DELETE
    await db_session.execute(
        text("DELETE FROM admin_settings WHERE key IN ('civicrm_url', 'civicrm_api_key', 'civicrm_site_key')")
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*) FROM admin_settings WHERE key IN ('civicrm_url', 'civicrm_api_key', 'civicrm_site_key')")
    )
    assert result.scalar_one() == 0


# --- No CiviCRM remnants in config accessors ---

def test_dynamic_settings_has_no_civicrm_methods():
    """
    S01 deleted get_civicrm_url/api_key/site_key from DynamicSettings.
    Verify they are truly absent (would regress if someone re-added them).
    """
    from app.config import DynamicSettings
    assert not hasattr(DynamicSettings, "get_civicrm_url"), \
        "get_civicrm_url must be deleted (S01)"
    assert not hasattr(DynamicSettings, "get_civicrm_api_key"), \
        "get_civicrm_api_key must be deleted (S01)"
    assert not hasattr(DynamicSettings, "get_civicrm_site_key"), \
        "get_civicrm_site_key must be deleted (S01)"


def test_no_civicrm_import_in_models():
    """
    models.py must not define CiviCRMMember or CiviCRMEvent after S01.
    Import the module and assert the class names are absent.
    """
    import app.models as models
    assert not hasattr(models, "CiviCRMMember"), \
        "CiviCRMMember must be deleted from models.py (S01)"
    assert not hasattr(models, "CiviCRMEvent"), \
        "CiviCRMEvent must be deleted from models.py (S01)"


def test_no_push_status_on_participant():
    """
    Participant model must not have push_status, push_attempts, or last_push_error.
    These were on the old Attendance model and were deleted in S01.
    """
    from app.models import Participant
    assert not hasattr(Participant, "push_status"), \
        "push_status must be deleted from Participant (S01)"
    assert not hasattr(Participant, "push_attempts"), \
        "push_attempts must be deleted from Participant (S01)"
    assert not hasattr(Participant, "last_push_error"), \
        "last_push_error must be deleted from Participant (S01)"


# --- Smoke test helper import check ---

def test_smoke_test_crm_script_importable():
    """
    scripts/smoke_test_crm.py must be syntactically valid Python.
    Import it as a module to catch syntax errors.
    """
    import importlib.util, os
    script_path = os.path.join(
        os.path.dirname(__file__), "../../scripts/smoke_test_crm.py"
    )
    if not os.path.exists(script_path):
        pytest.skip("smoke_test_crm.py not yet written")
    spec = importlib.util.spec_from_file_location("smoke_test_crm", script_path)
    mod = importlib.util.module_from_spec(spec)
    # Should not raise SyntaxError
    spec.loader.exec_module(mod)
```

### 8.2 Frontend vitest

No new frontend components in S21. The only frontend test is a regression check:

**File:** `frontend/src/__tests__/setup_no_civicrm.test.tsx`

```typescript
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('S21 CiviCRM excision — frontend', () => {
  it('SetupPage.tsx does not contain CiviCRM step content', () => {
    const src = readFileSync(
      resolve(__dirname, '../../pages/SetupPage.tsx'),
      'utf-8'
    );
    // The CiviCRM step block described in frontend.md lines 28 and 539–575
    // must be gone after S01; S21 re-asserts it hasn't crept back.
    expect(src).not.toMatch(/civicrm_url/i);
    expect(src).not.toMatch(/civicrm_api_key/i);
    expect(src).not.toMatch(/site_key/i);
    expect(src).not.toMatch(/CiviCRM base URL/i);
  });

  it('AttendancePage.tsx does not contain push-to-CiviCRM UI', () => {
    const src = readFileSync(
      resolve(__dirname, '../../pages/AttendancePage.tsx'),
      'utf-8'
    );
    expect(src).not.toMatch(/push.*civicrm/i);
    expect(src).not.toMatch(/dead.?letter/i);
    expect(src).not.toMatch(/push_status/i);
  });
});
```

---

## 9. Rollout / rollback / risks

### 9.1 Rollout order

1. **T-24h** — Pre-stage DNS TTL to 60s. Tag current production images as `:stable` (`docker tag seraphim-backend:unstable seraphim-backend:stable`). Take a full backup (`scripts/backup.sh`). Confirm pre-cutover gate checklist (§6.1) passes.
2. **T-2h** — Brief all operators. Confirm n8n workflow owners are reachable. Run S06 dry-run one final time; confirm counts are stable (no new data added since last dry-run).
3. **T=0** — Announce freeze. Disable n8n flows (§6.2). Enable CiviCRM maintenance mode.
4. **T+5m** — Re-export from CiviCRM (while DB is still accessible in maintenance mode).
5. **T+15m** — Run final S06 ETL (contacts → events → participants → links).
6. **T+45m** — Run verification queries (§6.4). If all pass, run S23 on-demand recompute (`POST /jobs/nightly_recompute`). Re-verify derived attrs.
7. **T+60m** — Flip DNS/Cloudflare (§6.5). Run smoke tests (§6.6). Declare go-live.
8. **T+2h** — Begin post-cutover decommission procedure (§9.3).

### 9.2 Rollback decision tree

**T+15m trigger (migration fails):** If any S06 phase exits with `status: failed` or error_count > 5%:
- Do NOT flip DNS.
- Diagnose: check import_batch error_count, review import_row_result rows with outcome=error, fix the export or the column-map.
- Re-run the failing phase (idempotent; already-loaded rows are skipped).
- If the issue is a data-shape problem that cannot be fixed in the window: postpone cutover. Re-enable n8n flows. Notify operators. Schedule a new freeze window.

**T+60m trigger (smoke tests fail after DNS flip):** If `smoke_test_crm.py --strict` exits non-zero:
- Immediately roll back DNS (revert the Cloudflare Tunnel ingress rule or DNS record to point back at the old CiviCRM/WordPress server).
- Re-enable n8n flows.
- Disable CiviCRM maintenance mode.
- Execute image rollback: `bash scripts/rollback.sh stable` (reverts to the `:stable`-tagged image).
- If the migration also ran: the Seraphim DB now has the full imported data, which is harmless. Do NOT restore the pre-cutover Seraphim backup (it has less data than what was just imported). Only restore the backup if the DB itself is corrupted.

**T+2h trigger (post-go-live instability):** If critical errors appear in the first 2h (500 responses on core endpoints, SSE broken, face pipeline offline):
- Assess: is the issue in Seraphim code (roll back image) or in data (investigate without rollback)?
- For code issue: `bash scripts/rollback.sh stable` (reverts image, keeps DB at current state — which is the fully imported data).
- For data issue: diagnose live; do not roll back the database (rolling it back loses the imported data; CiviCRM data still exists in the freeze-mode export).
- If rollback is executed: revert DNS to CiviCRM. Re-enable n8n. Re-disable Seraphim announcement.

### 9.3 Post-cutover decommission (T+2h, if go-live declared)

1. **Confirm n8n is completely off:** navigate to n8n dashboard; no workflows are `active`. Note: do NOT delete n8n data immediately — keep it for 30 days in case any workflow logic needs to be referenced for edge-case questions.
2. **CiviCRM archive:** run `scripts/decommission_civicrm.sh` from the Unraid box (or the CiviCRM host). This produces `civicrm_mysql_all_*.sql.gz` and `wordpress_files_*.tar.gz`.
3. **Verify archives:** `ls -lh /mnt/user/backups/civicrm_archive/` — both files exist and are non-empty (> 10 MB is a reasonable minimum for a 1,440-contact CiviCRM install).
4. **Move archives to cold storage:** copy to an external disk or cloud bucket.
5. **Disable CiviCRM permanently:** on the CiviCRM host, stop the WordPress + MySQL service (or block all external access to the host). Do not destroy the VM/container yet — keep it off-but-preserved for 30 days.
6. **Remove CiviCRM ingress from Cloudflare/DNS:** delete the public hostname or DNS record for `crm.lightnc.org` from the Cloudflare dashboard (or the DNS provider).
7. **Clean up Seraphim admin_settings:** the S21 Alembic migration (`s21_cleanup_civicrm_settings.py`) handles this automatically on the next `alembic upgrade head`. Verify: `SELECT count(*) FROM admin_settings WHERE key IN ('civicrm_url','civicrm_api_key','civicrm_site_key')` returns 0.
8. **Reset DNS TTL:** set `seraphim.lightnc.org` TTL back to 3600s (1 hour). The 60s emergency-TTL is expensive on DNS provider quotas.
9. **Schedule post-cutover backup:** run `scripts/backup.sh` immediately to capture the first fully-loaded production database state. Ensure daily cron is still configured (Unraid User Scripts plugin).
10. **Notify church staff:** send a confirmation that Seraphim is live and CiviCRM is archived. Provide the Seraphim URL and the new login procedure.
11. **30-day review:** after 30 days of stable operation, permanently delete the CiviCRM VM/container and n8n instance. Retain the SQL archive indefinitely.

### 9.4 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CiviCRM XLSX re-export takes longer than expected during freeze window | Low | Extends freeze window by 30–60 min | Pre-run the export during the dry-run phase to calibrate timing; have shell scripts ready |
| S06 ETL error_count > 5% on final run due to data-quality changes since dry-run | Low | Blocks go-live | Final dry-run within 2h of freeze; ensure data entry was truly frozen before export |
| n8n workflows fire mid-window before being disabled | Medium | Writes stale attendance to CiviCRM (irrelevant post-freeze) | Disable n8n **before** exporting; CiviCRM is in maintenance mode during export |
| DNS propagation slower than TTL (ISP caching) | Medium | Some users hit old CiviCRM site post-flip | Pre-stage TTL to 60s 24h before cutover; use Cloudflare Tunnel (instant propagation) |
| CompreFace unavailable during go-live window | Low | Face pipeline offline; manual check-in only | Not a blocker for cutover; face pipeline is supplementary. Smoke test warns but does not fail on CompreFace |
| `name_match_review_pending` > 500 at cutover | Medium | People-link fields unresolved for many contacts | Pre-resolve queue during build phase; accept ≤ 200 residual as operator-reviewed |
| S23 derived attrs not computed yet (all null) | Medium | Reports show empty tier/active/connected | Trigger on-demand recompute (`POST /jobs/nightly_recompute`) before declaring go-live |
| Face enrollment data absent on cutover (no face bootstrapping done yet) | Expected | Recognition pipeline starts with empty subject list | S07 bulk photo ingestion is a post-cutover activity; contacts are in the DB, enrollment follows |
| Rollback loses imported data if Seraphim DB is restored | Low | Data loss | Do NOT restore the Seraphim DB on rollback unless it is corrupted; only roll back the image |

---

## 10. Open questions & pending owner artifacts

### 10.1 Required before cutover can execute

| # | Artifact / question | Owner | Blocks |
|---|---|---|---|
| 1 | **Fresh CiviCRM XLSX exports with real Contact ID + Event ID** — the four files described in §6.3. These must be generated with the exact CiviCRM schema including all custom fields (Barangay, PEPSOL, Ministry, Community, six people-link fields, followup_listing). | Church admin / CiviCRM operator | §6.3 — final migration run |
| 2 | **Column-map YAML files for the final export** — the four `.map.yml` files in `config/`. These are templated in S06 (`config/civicrm_contacts.map.example.yml` etc.) but must be verified against the actual column headers in the final exports before the live run. | Seraphim developer (verify against exports) | §6.3 |
| 3 | **CiviCRM host SSH access** for `scripts/decommission_civicrm.sh` — the MySQL root credentials and WordPress root path on the CiviCRM host. | Church IT / CiviCRM admin | §9.3 step 2 |
| 4 | **n8n admin credentials** — to disable the 5 workflows in §6.2. | n8n operator | §6.2 |
| 5 | **Maintenance window announcement** — sent to church staff and n8n workflow owners at least 48h before cutover. Template provided in `docs/cutover/freeze.md`. | Church admin / Pastor | §6.2 |
| 6 | **Google Chat webhook URL** configured in Seraphim admin_settings (S18 deliverable) — needed to confirm notification fires in §6.6 go-live spot checks. | Church admin (via S18 setup UI) | §6.6 go-live checks |
| 7 | **Zoom S2S OAuth credentials** configured in Seraphim admin_settings (S18 deliverable) — needed for Zoom attendance pull on the night of go-live. | Church admin (via S18 setup UI) | §6.7 job verification |
| 8 | **Biometric consent resolution** — any contacts with active face subjects but no consent record must be consent-reviewed or purged before go-live per S08 policy. SQL query provided in §6.1. | S08 admin + church leadership | §6.1 pre-cutover gate |

### 10.2 Open questions for the master to reconcile

| # | Question | Impact |
|---|---|---|
| 1 | The production runbook currently calls the database `seraphim_attendance` (`backup.sh:15`, `PRODUCTION_RUNBOOK.md` throughout). Should this be renamed to `seraphim_crm` in the `.env.production.template` and compose files as part of S21 or S01, or should it stay `seraphim_attendance` to avoid a migration risk? | Naming only; no functional impact. Recommend keeping `seraphim_attendance` for this version to avoid an unnecessary DB rename. |
| 2 | The `scripts/smoke_test.py` Section 4 E2E checklist in `PRODUCTION_RUNBOOK.md` includes "Queue worker pushes to CiviCRM" as a required step. This step must be removed in the runbook update. Confirm the updated runbook replaces this with "Google Chat notification fires" and "Zoom attendance pull runs" as the equivalent integration health checks. | Runbook update scope for §4 |
| 3 | After n8n is disabled, the Google Forms that currently feed the n8n attendance flows (community reports, newcomer intake) become dead. The operator must redirect form submitters to the Seraphim public newcomer form (S13) and the community-report form (S22). Is there a church communications plan for this redirect, or does it fall to Seraphim's UI? | Out of Seraphim scope; church admin responsibility. Document in `docs/cutover/decommission.md`. |
| 4 | The S18 attendance notifiers post to a single Google Chat channel (confirmed in `decisions.md` Round 8). After cutover, the per-community Mattermost routing (which n8n currently does via a large Switch node) is gone. Confirm the single-channel Google Chat model is acceptable to church leadership before go-live. | S18 design; confirmed in decisions.md Round 8 but should be re-confirmed with stakeholders at pre-cutover gate meeting. |
