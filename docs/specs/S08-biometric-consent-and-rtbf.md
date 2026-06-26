# S08 — Biometric Consent & Right-to-be-Forgotten

**Phase:** C — Face-native (PRIORITY) · **Depends on:** S07 (face_samples, EnrollmentService, purge_subject_local seam, compreface_subjects columns), S01 (contacts table rename, audit_log, role enum admin|volunteer|viewer), S03 (ContactDetail page face-panel slot) · **Effort:** L · **Status:** Not started

> All symbols cite the real codebase. Code paths reference actual file:line values confirmed by reading `backend/app/models.py`, `backend/app/services/compreface.py` (compreface.py:172 `delete_subject`), `backend/app/services/face_cleanup.py` (face_cleanup.py:110–131 `_process_detection`), `backend/app/services/face_storage.py` (face_storage.py:63–65 enrolled layout), `backend/app/services/queue_manager.py`, `backend/app/config.py` (config.py:136 `get_face_retention_days`), `backend/app/dependencies.py`, `backend/app/main.py`. This spec targets the S01-renamed canonical model (`contacts`, `events`, `participants`); legacy names appear in parentheses where still present in code at implementation time.

---

## 1. Goal & rationale

Seraphim is the **system of record for biometric data**: face templates in CompreFace and face-crop images on disk under `data/enrolled/` and `data/faces/`. The church serves ~1,440 contacts in the Philippines, where the **Data Privacy Act of 2012 (RA 10173)** classifies biometrics as "sensitive personal information." The CRM must therefore:

1. **Record per-contact biometric consent** — when collected, who recorded it, the lawful-basis note.
2. **Enforce a configurable retention policy** — every consent record carries a `retention_until` timestamp; a scheduled daily job auto-purges expired or RTBF-requested records.
3. **Honor a deletion request (RTBF)** — a single staff-initiated action that atomically: (a) calls `ComprefaceClient.delete_subject(compreface_subject_id)` (`compreface.py:172`), (b) erases every enrolled-photo file from disk (layout: `data/enrolled/{compreface_subject_id}/sample_*_full.jpg` per `FaceStorage.save_enrollment`, `face_storage.py:63`), (c) anonymizes detection crops (`image_path=None`, `deleted_at=utc_now()`) for detections flagged `is_enrolled=True` for this subject, (d) deletes all `face_samples` rows (S07 table), (e) stamps `compreface_subjects.enrollment_status="purged"`, nulls `compreface_subject_id`, zeros `sample_count`, (f) stamps `biometric_consent.purged_at`. The **`contacts` row and all `participants` rows are never touched** — attendance history is preserved; only biometric data is erased.
4. **Audit every action immutably** — every consent write and every purge writes to `audit_log` (created in S01) with `before`/`after` JSONB snapshots.

S08 adds the governance layer **on top of** S07. It does not change enrollment mechanics. The purge sequence composes S07's `EnrollmentService.purge_subject_local` helper (which handles local file/sample/subject erasure) with the S08-owned CompreFace delete call and the `biometric_consent` stamping.

**Why Phase C:** S07 (enrollment) is the first sprint to write biometric data to production. Consent + RTBF must land in the same phase so the church never holds face data it cannot lawfully account for or irreversibly delete before go-live.

---

## 2. Scope

### In scope

- New `biometric_consent` table (1:1 with `contacts`, `UNIQUE(contact_id)`), per the canonical data model.
- One new column `consent_id` (FK from `compreface_subjects` to `biometric_consent`) added in this migration.
- `BiometricConsent` SQLAlchemy model + Pydantic DTOs in `backend/app/schemas.py`.
- `BiometricConsentService` (`backend/app/services/biometric_consent.py`) — record / update / revoke / request-deletion.
- `BiometricPurgeService` (`backend/app/services/biometric_purge.py`) — the RTBF purge engine (CompreFace delete + file erasure + DB erasure + consent stamping).
- `_delete_image_files` module-level helper extracted from `FaceCleanupService._process_detection` (`face_cleanup.py:110–131`) into `face_cleanup.py` module scope so both services share it.
- New router `backend/app/routers/biometric.py` with 7 endpoints.
- `_process_biometric_retention` worker method added to `QueueManager` (`queue_manager.py`), invoked from `run()` once per 24 h.
- **`biometric_retention_years` is read via `settings_service.get(db, "biometric_retention_years")` (not `DynamicSettings`).** The key is seeded by S16's migration with default `7`. S08 does not seed it and does not add a getter to `config.py`.
- **`enroll_without_consent` policy** is also read via `settings_service.get(db, "enroll_without_consent")` (default `false`). When `true`, enrollment proceeds without a prior consent record; a `# TODO(S08-policy)` comment in `EnrollmentService.enroll_contact_face` must check this flag. Seeded by S16.
- `audit_log` inserts for: `consent_recorded`, `consent_updated`, `consent_revoked`, `deletion_requested`, `biometric_purged`, `biometric_purge_failed`.
- S07 touch: in `EnrollmentService.enroll_contact_face` (`services/enrollment.py`), set `consent_id` on subject creation + leave `# TODO(S08-policy): block if no consent?` comment.
- Frontend: `ConsentPanel` component (mounts in S03 contact-detail below S07 `FacePanel`), `RecordConsentDialog`, `RetentionReport` admin page at `/settings/biometric`, typed service + types files.
- Role gating: viewer = read-only; volunteer = record/edit-note/revoke/request-deletion; admin = purge + edit `retention_until` + view retention report.
- Backend pytest, frontend vitest, `ruff check app`, `npm run build`, `npm run lint` all passing.

### Out of scope (explicit)

- Face enrollment / re-enrollment / `face_samples` / `photo_ingest_batches` / `EnrollmentService` core methods — S07.
- Contact soft-delete or hard-delete (`is_deleted` flag) — S01 / S11. RTBF erases **biometrics only**; the `contacts` row is untouched.
- Self-service member portal (all actions are staff-initiated per decisions.md Round 4).
- Non-biometric data subject access requests or general DSAR export — S05 / S19.
- Webhook / email notifications on purge — S17 (outbox).
- Hard enrollment gate (blocking `EnrollmentService.enroll_contact_face` until consent is recorded): S08 ships a warning banner only. The hard gate is a single-line change deferred to the owner's policy decision post-go-live (see §10.2).
- Consent for non-biometric personal data.

---

## 3. Data model changes

### 3.1 New table `biometric_consent`

One row per contact. Upsert on record/update; never create two rows for the same contact (enforced by `UNIQUE(contact_id)` at DB level with `with_for_update()` in the service).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `Integer` PK (identity/autoincrement) | no | — | app-minted |
| `contact_id` | `Integer` FK → `contacts.id` `ON DELETE CASCADE` | no | — | **UNIQUE** — 1:1 with contact; CASCADE so purging the contact row also purges its consent record |
| `consent_given` | `Boolean` | no | `server_default=true` | `false` after revocation; row is always kept post-revocation for the audit trail |
| `consented_at` | `DateTime` (naive UTC) | yes | `NULL` | when consent was collected; null if the row was backfilled for a "no consent on file" contact |
| `recorded_by_id` | `Integer` FK → `users.id` `ON DELETE SET NULL` | yes | `NULL` | system user who recorded consent |
| `basis_note` | `Text` | yes | `NULL` | free-text lawful-basis note, e.g. "Verbal consent at Sunday service 2026-06-01" |
| `retention_until` | `DateTime` (naive UTC) | yes | `NULL` | snapshotted at record time = `consented_at + timedelta(days=365 * retention_years)`; nullable = indefinite (set by admin explicitly) |
| `deletion_requested_at` | `DateTime` (naive UTC) | yes | `NULL` | set by the deletion-request endpoint; non-null = scheduled purge pending |
| `deletion_requested_by_id` | `Integer` FK → `users.id` `ON DELETE SET NULL` | yes | `NULL` | |
| `purged_at` | `DateTime` (naive UTC) | yes | `NULL` | stamped on purge completion; non-null = biometrics permanently erased |
| `purge_detail` | `JSON().with_variant(JSONB,"postgresql")` | yes | `NULL` | `{"subject_deleted": bool, "samples_deleted": int, "files_deleted": int, "errors": [...]}` |
| `created_at` | `DateTime` (naive UTC) | no | `utc_now` | |
| `updated_at` | `DateTime` (naive UTC) | no | `utc_now` (`onupdate=utc_now`) | |

Constraints and indexes:

- `UniqueConstraint("contact_id", name="uq_biometric_consent_contact")`
- `Index("ix_biometric_consent_retention", "retention_until", postgresql_where=sa.text("purged_at IS NULL"), sqlite_where=sa.text("purged_at IS NULL"))` — partial dual-dialect index (mirrors the pattern at `models.py:175-184` for `TaskAction`); drives the daily retention scan.
- `Index("ix_biometric_consent_deletion_requested", "deletion_requested_at", postgresql_where=sa.text("purged_at IS NULL"), sqlite_where=sa.text("purged_at IS NULL"))` — drives the RTBF-pending scan.

### 3.2 Modified table `compreface_subjects` — add `consent_id`

S07 already added `enrollment_source`, `last_trained_at`, `is_orphan`, `purged_at` columns to the model (see S07 §3.3). S08 adds one more:

| Column | Type | Null | Notes |
|---|---|---|---|
| `consent_id` | `Integer` FK → `biometric_consent.id` `ON DELETE SET NULL` | yes | Forward FK: convenience link from subject row to its governing consent. `SET NULL` so a purged/deleted consent row does not orphan the FK. Reverse lookup (consent → subjects) is always via `SELECT * FROM compreface_subjects WHERE contact_id = biometric_consent.contact_id`. |

Also: extend the `enrollment_status` string-column comment in `ComprefaceSubject` (`models.py:96`) to include `"purged"` as a terminal value. No new column — it is a string field; adding `"purged"` to the allowed set is a code-level change only.

### 3.3 `audit_log` (S01 table — used, not created here)

S08 inserts rows into `audit_log(id, actor_id FK→users, action, entity, entity_id, before JSONB, after JSONB, at)` created in S01. If S01 has not landed, the implementer must create `audit_log` first. Do not invent a local variant.

New `action` strings used in S08 (for `entity="biometric_consent"`):

| action | trigger |
|---|---|
| `consent_recorded` | POST record creates a new row |
| `consent_updated` | PATCH update (`basis_note` or `retention_until`) |
| `consent_revoked` | POST revoke |
| `deletion_requested` | POST deletion-request |
| `biometric_purged` | Successful purge (manual or scheduled) |
| `biometric_purge_failed` | Purge attempted but an unrecoverable exception occurred |

### 3.4 Alembic migration plan

One new revision file: `backend/alembic/versions/s08_add_biometric_consent.py`.

- `down_revision` = the revision id at the head of the S07 migration (read the S07 migration file header at implementation time — do NOT guess; never edit applied migrations per AGENTS.md).
- Import `JSON` from `sqlalchemy` and use `JSON().with_variant(JSONB_dialect, "postgresql")` for `purge_detail`, exactly as the module-level `JSONB` alias at `models.py:22`.

**Upgrade ops (in order):**

```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

op.create_table(
    "biometric_consent",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column(
        "contact_id",
        sa.Integer(),
        sa.ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "consent_given",
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    ),
    sa.Column("consented_at", sa.DateTime(), nullable=True),
    sa.Column(
        "recorded_by_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("basis_note", sa.Text(), nullable=True),
    sa.Column("retention_until", sa.DateTime(), nullable=True),
    sa.Column("deletion_requested_at", sa.DateTime(), nullable=True),
    sa.Column(
        "deletion_requested_by_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("purged_at", sa.DateTime(), nullable=True),
    sa.Column(
        "purge_detail",
        sa.JSON().with_variant(pg.JSONB(), "postgresql"),
        nullable=True,
    ),
    sa.Column(
        "created_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
)
op.create_unique_constraint(
    "uq_biometric_consent_contact", "biometric_consent", ["contact_id"]
)
op.create_index(
    "ix_biometric_consent_retention",
    "biometric_consent",
    ["retention_until"],
    postgresql_where=sa.text("purged_at IS NULL"),
    sqlite_where=sa.text("purged_at IS NULL"),
)
op.create_index(
    "ix_biometric_consent_deletion_requested",
    "biometric_consent",
    ["deletion_requested_at"],
    postgresql_where=sa.text("purged_at IS NULL"),
    sqlite_where=sa.text("purged_at IS NULL"),
)
# Forward FK on compreface_subjects → biometric_consent
op.add_column(
    "compreface_subjects",
    sa.Column(
        "consent_id",
        sa.Integer(),
        sa.ForeignKey("biometric_consent.id", ondelete="SET NULL"),
        nullable=True,
    ),
)
```

**Data backfill in migration:** none. The schema additions are purely additive. The optional operational backfill that creates `consent_given=false` rows for all currently-enrolled contacts is a manual script run after migration (see §6) — not a migration step, because it involves application logic.

**Downgrade ops:**

```python
op.drop_column("compreface_subjects", "consent_id")
op.drop_index("ix_biometric_consent_deletion_requested", table_name="biometric_consent")
op.drop_index("ix_biometric_consent_retention", table_name="biometric_consent")
op.drop_constraint("uq_biometric_consent_contact", "biometric_consent", type_="unique")
op.drop_table("biometric_consent")
```

> **WARNING:** downgrade after go-live is irreversible — all consent records are permanently lost. The migration docstring must state this loudly. Purged biometric data cannot be recovered regardless (that is the RTBF guarantee). Only use in dev/staging.

---

## 4. Backend

### 4.1 Endpoints

New router `backend/app/routers/biometric.py`, registered in `main.py` with `prefix="/biometric"`, `tags=["biometric"]`, gated by `check_setup_complete` (same pattern as `pit.router`, `main.py:113`). No `/api` prefix (nginx strips it per AGENTS.md).

Auth uses existing deps from `backend/app/dependencies.py`: `get_current_user` (any authenticated role), `require_volunteer` (admin + volunteer), `require_admin` (admin only).

| METHOD | Path | Role | Request Schema | Response Schema | Notes |
|---|---|---|---|---|---|
| GET | `/biometric/contacts/{contact_id}/consent` | any-auth | — | `ConsentResponse` | Returns the consent row if it exists; synthesizes a `status="none"` object with all nulls if no row. Includes derived `enrolled_photo_count` (COUNT of `face_samples` for contact) and `subject_active` (bool: `compreface_subjects.enrollment_status=="active"` for this contact). Never 404 on no-row (returns 200 with `status="none"`). |
| POST | `/biometric/contacts/{contact_id}/consent` | volunteer+ | `ConsentRecordRequest` | `ConsentResponse` | Upsert via `with_for_update()`: create if no row; update if row exists and `purged_at IS NULL`. Sets `consent_given=true`, `consented_at=utc_now()`, `recorded_by_id`, computes `retention_until`. Writes `audit_log(consent_recorded)` on create; `audit_log(consent_updated)` on update. Returns **409** if `purged_at` already set (re-enrollment consent requires owner review). |
| PATCH | `/biometric/contacts/{contact_id}/consent` | volunteer+ (`retention_until` admin-only) | `ConsentUpdateRequest` | `ConsentResponse` | Edits `basis_note` (volunteer+); edits `retention_until` (admin-only: 403 if volunteer provides it). Writes `audit_log(consent_updated)`. Returns 404 if no consent row exists yet. |
| POST | `/biometric/contacts/{contact_id}/consent/revoke` | volunteer+ | `ConsentRevokeRequest` | `ConsentResponse` | Sets `consent_given=false`. Does **not** purge. Writes `audit_log(consent_revoked)`. UI surfaces a nudge toward the deletion-request flow. 404 if no consent row. |
| POST | `/biometric/contacts/{contact_id}/deletion-request` | volunteer+ | `DeletionRequest` | `ConsentResponse` (or `PurgeResultResponse` if `immediate=true`) | Sets `deletion_requested_at=utc_now()`, `deletion_requested_by_id`. If `immediate=true` **and** caller is admin: runs `BiometricPurgeService.purge_contact` synchronously and returns `PurgeResultResponse`. Returns **404** when the contact has no `compreface_subjects` row, no `face_samples`, and no consent row (nothing to forget). Writes `audit_log(deletion_requested)` + (if immediate) `audit_log(biometric_purged)`. |
| POST | `/biometric/contacts/{contact_id}/purge` | **admin** | — | `PurgeResultResponse` | Force-run the purge now. Idempotent: if `purged_at` already set **and** `purge_detail.subject_deleted==true`, returns 200 with stored result without re-calling CompreFace, writes no new audit row. If `subject_deleted==false` (prior CompreFace failure), re-attempts the CompreFace delete only (files/DB already erased). Writes `audit_log(biometric_purged)` or `audit_log(biometric_purge_failed)`. |
| GET | `/biometric/retention/report` | **admin** | Query: `within_days:int=30`, `include_purged:bool=false`, `limit:int=50`, `offset:int=0` | `RetentionReportResponse` | Consents whose `retention_until <= utc_now() + timedelta(days=within_days)` OR `deletion_requested_at IS NOT NULL`. Excludes `purged_at IS NOT NULL` unless `include_purged=true`. Paginated/clamped (max limit 200). Sorted by `retention_until ASC NULLS LAST`. Joins to `contacts` for `contact_name`. |

### 4.2 Services and workers

#### `BiometricConsentService` — `backend/app/services/biometric_consent.py` (NEW)

Stateless class; each method receives `session: AsyncSession` as its first parameter (follows the existing service pattern — no session stored on `self`). Type-hint all public functions per AGENTS.md.

```python
from __future__ import annotations
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog, BiometricConsent, ComprefaceSubject, FaceSample, User, utc_now
)
from app.config import dynamic_settings


@dataclasses.dataclass
class ConsentDerived:
    consent: Optional[BiometricConsent]
    enrolled_photo_count: int
    subject_active: bool


class BiometricConsentService:

    async def get_with_derived(
        self, session: AsyncSession, contact_id: int
    ) -> ConsentDerived:
        """Load consent row + derived counts. Never raises 404."""
        consent = await session.scalar(
            select(BiometricConsent).where(
                BiometricConsent.contact_id == contact_id
            )
        )
        enrolled_count = await session.scalar(
            select(func.count(FaceSample.id)).where(
                FaceSample.contact_id == contact_id
            )
        ) or 0
        subject = await session.scalar(
            select(ComprefaceSubject).where(
                ComprefaceSubject.contact_id == contact_id
            )
        )
        subject_active = (
            subject is not None and subject.enrollment_status == "active"
        )
        return ConsentDerived(consent, enrolled_count, subject_active)

    async def record(
        self,
        session: AsyncSession,
        contact_id: int,
        *,
        recorded_by_id: int,
        basis_note: Optional[str],
        retention_years: Optional[int],
        actor_id: int,
    ) -> BiometricConsent:
        """Create or update (upsert) consent. 409 if purged_at is set."""
        now = utc_now()
        effective_years = retention_years or dynamic_settings.get_biometric_retention_years()
        retention_until = now + timedelta(days=365 * effective_years)

        existing = await session.scalar(
            select(BiometricConsent)
            .where(BiometricConsent.contact_id == contact_id)
            .with_for_update()
        )
        if existing is not None:
            if existing.purged_at is not None:
                from fastapi import HTTPException
                raise HTTPException(
                    409,
                    "Biometric data was purged for this contact; consent cannot "
                    "be re-recorded without a new RTBF process.",
                )
            before = _consent_snapshot(existing)
            existing.consent_given = True
            existing.consented_at = now
            existing.recorded_by_id = recorded_by_id
            existing.basis_note = basis_note
            existing.retention_until = retention_until
            existing.updated_at = now
            await session.flush()
            self._audit(session, actor_id, "consent_updated",
                        existing.id, before, _consent_snapshot(existing))
            await session.commit()
            return existing

        row = BiometricConsent(
            contact_id=contact_id,
            consent_given=True,
            consented_at=now,
            recorded_by_id=recorded_by_id,
            basis_note=basis_note,
            retention_until=retention_until,
        )
        session.add(row)
        await session.flush()   # populate row.id before audit
        self._audit(session, actor_id, "consent_recorded",
                    row.id, None, _consent_snapshot(row))
        await session.commit()
        return row

    async def update(
        self,
        session: AsyncSession,
        contact_id: int,
        *,
        basis_note: Optional[str] = None,
        retention_until: Optional[datetime] = None,
        actor_id: int,
        is_admin: bool,
    ) -> BiometricConsent:
        from fastapi import HTTPException
        if retention_until is not None and not is_admin:
            raise HTTPException(403, "Only admins may edit retention_until.")
        row = await session.scalar(
            select(BiometricConsent)
            .where(BiometricConsent.contact_id == contact_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "No consent record for this contact.")
        before = _consent_snapshot(row)
        if basis_note is not None:
            row.basis_note = basis_note
        if retention_until is not None:
            row.retention_until = retention_until
        row.updated_at = utc_now()
        await session.flush()
        self._audit(session, actor_id, "consent_updated",
                    row.id, before, _consent_snapshot(row))
        await session.commit()
        return row

    async def revoke(
        self,
        session: AsyncSession,
        contact_id: int,
        *,
        reason: Optional[str],
        actor_id: int,
    ) -> BiometricConsent:
        from fastapi import HTTPException
        row = await session.scalar(
            select(BiometricConsent)
            .where(BiometricConsent.contact_id == contact_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "No consent record for this contact.")
        before = _consent_snapshot(row)
        row.consent_given = False
        row.updated_at = utc_now()
        if reason:
            existing_note = row.basis_note or ""
            row.basis_note = f"{existing_note}\nRevoke reason: {reason}".strip()
        await session.flush()
        self._audit(session, actor_id, "consent_revoked",
                    row.id, before, _consent_snapshot(row))
        await session.commit()
        return row

    async def request_deletion(
        self,
        session: AsyncSession,
        contact_id: int,
        *,
        actor_id: int,
        reason: Optional[str],
    ) -> BiometricConsent:
        from fastapi import HTTPException
        from sqlalchemy import select, func
        # 404 if truly nothing to forget
        subject_count = await session.scalar(
            select(func.count(ComprefaceSubject.id)).where(
                ComprefaceSubject.contact_id == contact_id
            )
        ) or 0
        sample_count = await session.scalar(
            select(func.count(FaceSample.id)).where(
                FaceSample.contact_id == contact_id
            )
        ) or 0
        row = await session.scalar(
            select(BiometricConsent)
            .where(BiometricConsent.contact_id == contact_id)
            .with_for_update()
        )
        if subject_count == 0 and sample_count == 0 and row is None:
            raise HTTPException(404, "No biometric data on file for this contact.")
        now = utc_now()
        if row is None:
            row = BiometricConsent(
                contact_id=contact_id,
                consent_given=False,
                deletion_requested_at=now,
                deletion_requested_by_id=actor_id,
            )
            session.add(row)
            await session.flush()
        else:
            before = _consent_snapshot(row)
            row.deletion_requested_at = now
            row.deletion_requested_by_id = actor_id
            if reason:
                note = row.basis_note or ""
                row.basis_note = f"{note}\nDeletion reason: {reason}".strip()
            row.updated_at = now
            await session.flush()
            self._audit(session, actor_id, "deletion_requested",
                        row.id, before, _consent_snapshot(row))
        await session.commit()
        return row

    def _audit(
        self,
        session: AsyncSession,
        actor_id: Optional[int],
        action: str,
        entity_id: int,
        before: Optional[dict],
        after: Optional[dict],
    ) -> None:
        """Queue an AuditLog row. Caller must flush/commit."""
        from app.models import AuditLog
        row = AuditLog(
            actor_id=actor_id,
            action=action,
            entity="biometric_consent",
            entity_id=entity_id,
            before=before,
            after=after,
            at=utc_now(),
        )
        session.add(row)


def _consent_snapshot(c: BiometricConsent) -> dict:
    """Return a JSON-serializable dict of mutable consent fields for audit."""
    return {
        "consent_given": c.consent_given,
        "consented_at": c.consented_at.isoformat() if c.consented_at else None,
        "recorded_by_id": c.recorded_by_id,
        "basis_note": c.basis_note,
        "retention_until": c.retention_until.isoformat() if c.retention_until else None,
        "deletion_requested_at": (
            c.deletion_requested_at.isoformat() if c.deletion_requested_at else None
        ),
        "purged_at": c.purged_at.isoformat() if c.purged_at else None,
    }
```

Key business rules:
- **Naive UTC timestamps** throughout: `utc_now()` from `models.py:28`; comparisons use `datetime.now(timezone.utc).replace(tzinfo=None)` per AGENTS.md database conventions.
- **Retention computation**: `timedelta(days=365 * years)` — document the ~0.25 day/year leap-year approximation in docstring; do **not** introduce `python-dateutil`.
- **`with_for_update()`** on all mutating loads to prevent double-purge races (mirrors `task_service.py` per AGENTS.md concurrent-approval pattern).

---

#### `BiometricPurgeService` — `backend/app/services/biometric_purge.py` (NEW)

Constructor: `__init__(self, storage_base: Path)` — same pattern as `FaceCleanupService.__init__` (`face_cleanup.py:32`).

```python
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BiometricConsent, ComprefaceSubject, Detection, FaceSample, utc_now
)
from app.services.compreface import ComprefaceClient
from app.services.face_cleanup import _delete_image_files

logger = logging.getLogger(__name__)


@dataclass
class PurgeResult:
    contact_id: int
    purged_at: datetime
    subject_deleted: bool
    samples_deleted: int
    files_deleted: int
    errors: list[str] = field(default_factory=list)
```

**`async def purge_contact(self, session: AsyncSession, contact_id: int, *, actor_id: Optional[int]) -> PurgeResult`**

Ordered so local erasure is never blocked by CompreFace availability:

**Step 0 — idempotency check:**
Load `BiometricConsent` for `contact_id`. If `purged_at IS NOT NULL` AND `purge_detail["subject_deleted"] == True`: return the stored result immediately; write no audit row; do not call CompreFace.
Special partial-retry case: if `purged_at IS NOT NULL` AND `purge_detail["subject_deleted"] == False` (prior CompreFace failure): skip to Step 6 (re-attempt CompreFace only); skip Steps 1–5.

**Step 1 — read target data (read-only):**
```python
subjects = (await session.scalars(
    select(ComprefaceSubject).where(ComprefaceSubject.contact_id == contact_id)
)).all()
samples = (await session.scalars(
    select(FaceSample).where(FaceSample.contact_id == contact_id)
)).all()
```

**Step 2 — delete enrolled-photo files:**
For each sample in `samples`: call `_delete_image_files(self.storage_base, sample.image_path)` and `_delete_image_files(self.storage_base, sample.thumb_path)`. Accumulate `files_deleted` and `errors`. After per-sample deletion, attempt to `rmdir` the per-subject directory:
```python
for subj in subjects:
    if subj.compreface_subject_id:
        dir_path = self.storage_base / "enrolled" / subj.compreface_subject_id
        try:
            if dir_path.is_dir() and not any(dir_path.iterdir()):
                dir_path.rmdir()
        except OSError as exc:
            logger.error("rmdir %s: %s", dir_path, exc)
```
This matches the enrolled-directory layout from `FaceStorage.save_enrollment` (`face_storage.py:63–65`).

**Step 3 — anonymize linked detection crops:**
```python
detections_to_purge = (await session.scalars(
    select(Detection)
    .where(Detection.compreface_subject_id.in_(
        [s.compreface_subject_id for s in subjects if s.compreface_subject_id]
    ))
    .where(Detection.is_enrolled == True)
    .where(Detection.deleted_at.is_(None))
)).all()
for det in detections_to_purge:
    cnt, errs = _delete_image_files(self.storage_base, det.image_path)
    files_deleted += cnt
    errors.extend(errs)
    det.image_path = None
    det.deleted_at = utc_now()
```
This mirrors the `FaceCleanupService._process_detection` logic (`face_cleanup.py:113–131`) to erase detection crops that were the evidence for enrollment.

**Step 4 — delete `face_samples` rows:**
```python
await session.execute(
    delete(FaceSample).where(FaceSample.contact_id == contact_id)
)
samples_deleted = len(samples)
```

**Step 5 — update `compreface_subjects`:**
For each subject row: capture `saved_subject_id = subj.compreface_subject_id` (needed for CompreFace call in Step 6 after nulling). Then:
```python
subj.enrollment_status = "purged"
subj.compreface_subject_id = None   # prevents re-recognition matching
subj.sample_count = 0
subj.last_trained_at = None
```
After `session.flush()`, the `compreface_subject_id` in CompreFace (the string id) is no longer referenced by any local `compreface_subjects` row, so the live pipeline's `_find_member_id` (`face_pipeline.py:26–33`) can never map a future recognition match to this contact.

**Step 6 — call CompreFace `delete_subject`:**
```python
compreface = ComprefaceClient()
subject_deleted = True
try:
    for saved_id in saved_subject_ids:
        ok = await compreface.delete_subject(saved_id)
        # CompreFace 404 means already deleted externally → treat as success
        if not ok:
            errors.append(f"CompreFace delete_subject({saved_id!r}) returned non-2xx")
            subject_deleted = False
except Exception as exc:
    errors.append(f"CompreFace error: {exc}")
    subject_deleted = False
    logger.exception("CompreFace delete_subject failed for contact_id=%s", contact_id)
finally:
    await compreface.close()
```
`delete_subject` is at `compreface.py:172` and returns `bool` (200/204 → True). Tolerate failure: local erasure already complete; the stale CompreFace subject cannot match any contact (its local `compreface_subject_id` is now null) so it becomes a dangling orphan that can be cleaned up via the `POST /enrollment/backfill` admin tool.

**Step 7 — stamp `biometric_consent`:**
```python
now = utc_now()
consent = await session.scalar(
    select(BiometricConsent)
    .where(BiometricConsent.contact_id == contact_id)
    .with_for_update()
)
if consent is None:
    consent = BiometricConsent(contact_id=contact_id, consent_given=False)
    session.add(consent)
    await session.flush()
consent.purged_at = now
consent.purge_detail = {
    "subject_deleted": subject_deleted,
    "samples_deleted": samples_deleted,
    "files_deleted": files_deleted,
    "errors": errors,
}
if consent.deletion_requested_at is None:
    # Admin-forced purge without prior deletion request
    consent.deletion_requested_at = now
    consent.deletion_requested_by_id = actor_id
consent.updated_at = now
```

**Step 8 — write audit_log + commit:**
```python
from app.services.biometric_consent import _consent_snapshot, BiometricConsentService
BiometricConsentService()._audit(
    session, actor_id,
    "biometric_purged" if not errors else "biometric_purge_failed",
    consent.id,
    None,  # before already captured if needed
    {"purge_detail": consent.purge_detail},
)
await session.commit()
return PurgeResult(
    contact_id=contact_id,
    purged_at=now,
    subject_deleted=subject_deleted,
    samples_deleted=samples_deleted,
    files_deleted=files_deleted,
    errors=errors,
)
```

**On unrecoverable exception** (e.g., DB commit failure): `await session.rollback()`. Write `audit_log(biometric_purge_failed)` in a new session (best-effort). The router converts this to `HTTPException(502)`.

**Edge cases:**
- Contact with no `compreface_subjects` but with `face_samples`: Steps 2–4 run; Steps 5–6 are no-ops.
- Contact with no biometric data at all: `purge_contact` stamps `purged_at` to block future enrollment; returns `samples_deleted=0, files_deleted=0`.
- Multiple subjects per contact: iterate all in Steps 5–6 (canonical model aims for one, but edge case is handled).
- Concurrency: `with_for_update()` on the consent load in Step 7; second parallel purge finds `purged_at` already set and returns idempotently from Step 0.

---

#### `_delete_image_files` helper — `backend/app/services/face_cleanup.py` (EXTRACT)

Extract to module scope from `FaceCleanupService._process_detection` (`face_cleanup.py:110–131`). Both `FaceCleanupService` and `BiometricPurgeService` import this function:

```python
def _delete_image_files(
    storage_base: Path, image_path: Optional[str]
) -> tuple[int, list[str]]:
    """Delete the full + thumb file pair for a stored image path.

    The stored path is always the thumb (``_thumb.jpg``); the companion full-
    resolution file is derived by replacing ``_thumb.jpg`` with ``_full.jpg``
    (matches ``FaceStorage.save_detection``, face_storage.py:44–51).

    Returns (count_deleted, errors). Never raises; logs + appends errors.
    Does NOT modify any DB row.
    """
    if not image_path:
        return 0, []
    thumb = storage_base / image_path
    full = Path(str(thumb).replace("_thumb.jpg", "_full.jpg"))
    deleted = 0
    errors: list[str] = []
    for path in (thumb, full):
        try:
            if path.exists():
                path.unlink()
                deleted += 1
        except OSError as exc:
            msg = f"Failed to delete {path}: {exc}"
            logger.error(msg)
            errors.append(msg)
    return deleted, errors
```

Refactor `FaceCleanupService._process_detection` (`face_cleanup.py:110–131`) to call this helper for all file-deletion logic (pure internal refactor; no behavior change; existing tests must pass unchanged).

---

#### Worker integration — `backend/app/services/queue_manager.py` (MODIFY)

Add `_process_biometric_retention` after the three surviving jobs (`_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup`). Add `_last_retention_run: datetime | None = None` to `QueueManager.__init__`. Call from `run()` in the same async cycle.

```python
async def _process_biometric_retention(self) -> bool:
    """Run at most once per 24 h. Purge consents that are expired or RTBF-requested."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        self._last_retention_run is not None
        and (now - self._last_retention_run).total_seconds() < 86_400
    ):
        return False
    self._last_retention_run = now
    purged_any = False

    async with self.db_session_factory() as session:
        rows = (await session.scalars(
            select(BiometricConsent)
            .where(BiometricConsent.purged_at.is_(None))
            .where(
                (BiometricConsent.deletion_requested_at.isnot(None))
                | (BiometricConsent.retention_until < now)
            )
            .limit(100)
        )).all()

        purge_svc = BiometricPurgeService(
            storage_base=Path(legacy_settings.STORAGE_PATH)
        )
        for consent in rows:
            try:
                await purge_svc.purge_contact(
                    session, consent.contact_id, actor_id=None
                )
                purged_any = True
            except Exception:
                logger.exception(
                    "Biometric retention purge failed for contact_id=%s",
                    consent.contact_id,
                )
                purged_any = True  # ran this cycle

    return purged_any
```

Add imports to `queue_manager.py`:
```python
from app.models import BiometricConsent  # add alongside existing model imports
from app.services.biometric_purge import BiometricPurgeService
```

**S16 integration note**: if S16 (APScheduler + `job_runs`) has already landed when S08 is implemented, register `_process_biometric_retention` as an APScheduler cron job tracked in `job_runs` instead of using the `_last_retention_run` in-memory guard. The `_last_retention_run` pattern mirrors `_last_cleanup_run` (wherever that is in `queue_manager.py`) and is the safe fallback until S16.

---

#### Config — `backend/app/config.py` (MODIFY)

Add after `get_face_retention_days` (`config.py:136`):

```python
def get_biometric_retention_years(self) -> int:
    return self.get_int("biometric_retention_years", 7)
```

---

#### S07 enrollment touch — `backend/app/services/enrollment.py`

In `EnrollmentService.enroll_contact_face`, at the point where `ComprefaceSubject` is created (the upsert / new-row path), add:

```python
# Link to consent record if one exists (S08)
from app.services.biometric_consent import BiometricConsentService
consent = await BiometricConsentService().get_with_derived(self.session, contact_id)
subject.consent_id = consent.consent.id if consent.consent else None
# TODO(S08-policy): block enrollment here if consent.consent is None or not consent.consent.consent_given
```

This is a no-op at runtime when S07 ships before S08 (the column will not yet exist); the full link is activated when the S08 migration is applied.

---

#### Settings — `biometric_retention_years` (owned by S16)

**Do not seed `biometric_retention_years` in `setup.py`.** This key is seeded by S16's migration (`s16_job_runs_and_settings_engine`) with default `7`. S08 reads it at runtime:

```python
retention_years = await settings_service.get(db, "biometric_retention_years")  # int, default 7
```

S08 implementer: ensure S16 has been applied before S08 (the key must exist). S08's migration has no `admin_settings` seeds.

### 4.3 File-by-file change list

**CREATE:**
- `backend/app/routers/biometric.py` — 7 endpoints; imports `BiometricConsentService`, `BiometricPurgeService`, `require_admin`, `require_volunteer`, `get_current_user`, `check_setup_complete`.
- `backend/app/services/biometric_consent.py` — `BiometricConsentService` class, `ConsentDerived` dataclass, `_consent_snapshot` helper.
- `backend/app/services/biometric_purge.py` — `BiometricPurgeService` class, `PurgeResult` dataclass.
- `backend/alembic/versions/s08_add_biometric_consent.py` — migration (§3.4).
- `backend/tests/test_biometric_consent.py` — service + endpoint tests (§8).
- `backend/tests/test_biometric_purge.py` — purge-engine tests (§8).

**MODIFY:**
- `backend/app/models.py`
  - Add `class BiometricConsent(Base)` with all columns, constraints, and dual-dialect partial indexes (use the module-level `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` alias at `models.py:22`; follow `AdminSetting.value` at `models.py:283` as the pattern for the JSONB column).
  - Add `consent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("biometric_consent.id", ondelete="SET NULL"), nullable=True)` to `ComprefaceSubject` (`models.py:87`).
  - Update `ComprefaceSubject.enrollment_status` comment (`models.py:96`) to include `"purged"`.
- `backend/app/schemas.py` — add DTOs (§4.4 below).
- `backend/app/main.py` — import and include `biometric.router`:
  ```python
  from app.routers import biometric as biometric_router
  app.include_router(biometric_router.router, dependencies=[Depends(check_setup_complete)])
  ```
  (Insert after `analytics.router` include, before `storage_router`, per existing order pattern in `main.py:106-118`.)
- `backend/app/config.py` — add `get_biometric_retention_years()` after `get_face_retention_days` (`config.py:136`).
- `backend/app/services/queue_manager.py` — add `_process_biometric_retention`, `_last_retention_run`, and imports; call from `run()`.
- `backend/app/services/face_cleanup.py` — extract `_delete_image_files` to module scope; refactor `_process_detection` (`face_cleanup.py:110-131`) to call it.
- `backend/app/routers/setup.py` — seed `biometric_retention_years` admin_setting.
- `backend/app/services/enrollment.py` (S07 file) — set `consent_id` on subject creation + `# TODO(S08-policy)` comment.

**DELETE:** none.

### 4.4 Pydantic schemas — additions to `backend/app/schemas.py`

```python
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ConsentRecordRequest(BaseModel):
    basis_note: Optional[str] = None
    retention_years: Optional[int] = Field(None, ge=1, le=50)


class ConsentUpdateRequest(BaseModel):
    basis_note: Optional[str] = None
    retention_until: Optional[datetime] = None  # admin-only; 403 for volunteer


class ConsentRevokeRequest(BaseModel):
    reason: Optional[str] = None


class DeletionRequest(BaseModel):
    reason: Optional[str] = None
    immediate: bool = False  # admin-only meaningful; non-admin ignores


class ConsentResponse(BaseModel):
    contact_id: int
    status: Literal["none", "consented", "revoked", "deletion_requested", "purged"]
    # derived server-side: "none" if no row; "purged" if purged_at set; "deletion_requested"
    # if deletion_requested_at set; "revoked" if consent_given=false; else "consented"
    consent_given: Optional[bool] = None
    consented_at: Optional[datetime] = None
    recorded_by_name: Optional[str] = None   # joined from users.name
    basis_note: Optional[str] = None
    retention_until: Optional[datetime] = None
    deletion_requested_at: Optional[datetime] = None
    deletion_requested_by_name: Optional[str] = None
    purged_at: Optional[datetime] = None
    enrolled_photo_count: int = 0   # COUNT(face_samples WHERE contact_id=...)
    subject_active: bool = False    # compreface_subjects.enrollment_status=="active"


class PurgeResultResponse(BaseModel):
    contact_id: int
    purged_at: datetime
    subject_deleted: bool
    samples_deleted: int
    files_deleted: int
    errors: List[str] = []


class RetentionReportItem(BaseModel):
    contact_id: int
    contact_name: str             # joined from contacts first_name + last_name
    status: str
    consent_given: Optional[bool] = None
    consented_at: Optional[datetime] = None
    retention_until: Optional[datetime] = None
    deletion_requested_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None
    enrolled_photo_count: int = 0


class RetentionReportResponse(BaseModel):
    items: List[RetentionReportItem]
    total: int
    limit: int
    offset: int
```

**Deriving `status` in the router:**
```python
def _derive_status(c: Optional[BiometricConsent]) -> str:
    if c is None:
        return "none"
    if c.purged_at is not None:
        return "purged"
    if c.deletion_requested_at is not None:
        return "deletion_requested"
    if not c.consent_given:
        return "revoked"
    return "consented"
```

---

## 5. Frontend

### 5.1 Pages, routes, and components

#### New: `frontend/src/components/biometric/ConsentPanel.tsx`

Props: `{ contactId: number; contactName: string }`. Mounts in the S03 Contact Detail page below the S07 `<FacePanel>`.

TanStack Query: `queryKey: ["biometric", "consent", contactId]`, `queryFn: () => biometricService.getConsent(contactId)`.

Layout (mobile-first, 375px base; `md:flex md:items-start md:gap-4` for sidebar-style on desktop):

**Status pill:**
```
none              → bg-muted text-muted-foreground rounded-full px-3 py-1 text-xs  "No consent on file"
consented         → text-green-600 dark:text-green-400 (border-green-300) "Consent recorded"
revoked           → text-amber-600 dark:text-amber-400 "Consent revoked"
deletion_requested → text-orange-600 dark:text-orange-400 "Pending deletion"
purged            → text-red-600 dark:text-red-400 "Face data purged"
```

**Warning banner** (shown when `subject_active && status === "none"`):
```
bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-700
text-amber-800 dark:text-amber-200 rounded-lg p-3 text-sm
```
Copy: "Face recognition is active but no biometric consent is on file. Please record consent before go-live."

**Meta row** (shown when `status !== "none"`): "Recorded by {recorded_by_name} on {consented_at}"; "Retention until {retention_until}"; "{enrolled_photo_count} enrolled photo(s)".

**Action buttons** (role-gated, ≥44px touch target, `min-h-[44px]`):
- **Record consent** (volunteer+, hidden if `status === "purged"`): opens `RecordConsentDialog`.
- **Edit** (volunteer+, hidden if purged): opens `RecordConsentDialog` pre-filled. `retention_until` input disabled for volunteers.
- **Revoke** (volunteer+, shown if `status === "consented"`): `ConfirmDialog("Revoke biometric consent? This does not delete face data. Use 'Request deletion' to erase face data.")`. On confirm: `biometricService.revokeConsent(contactId)`.
- **Request deletion** (volunteer+, hidden if purged): `ConfirmDialog` — copy: `"Request deletion of all face biometric data for ${contactName}? This will be processed by the system and cannot be undone."`. On confirm: `biometricService.requestDeletion(contactId, { reason: "" })`.
- **Purge now** (admin only, hidden if `status === "purged"`): double-confirm: first `ConfirmDialog` — `"Permanently and irreversibly delete all face data for ${contactName}? Attendance history is preserved. This cannot be undone."`; on confirm calls `biometricService.purgeNow(contactId)`. On success: `toast.success("Face data purged for " + contactName)`, invalidate `["biometric","consent",contactId]` AND `["contact-faces", contactId]` (S07 FacePanel must clear enrolled thumbnails).

**LoadingState / EmptyState / ErrorState** from `frontend/src/components/ui/StateViews.tsx`.

All errors: `toast.error(err.response?.data?.detail ?? "An unexpected error occurred")` per AGENTS.md.

---

#### New: `frontend/src/components/biometric/RecordConsentDialog.tsx`

Controlled modal (props: `open: boolean; onClose: () => void; contactId: number; initialData?: ConsentResponse`).

Mobile: full-screen overlay. Desktop: centered dialog (max-w-md).

Fields:
- `Basis note` — `<textarea>` (optional). Placeholder: "e.g., Verbal consent given Sunday service 2026-06-01, documented in paper register."
- `Retention years` — `<input type="number">` (default: 7). Visible and editable for admin. For volunteers: read-only `<span>` showing "7 years (system default)".

On submit: `POST /biometric/contacts/{id}/consent` (create) or `PATCH` (edit, when `initialData` is provided with an existing consent row). Invalidates `["biometric","consent",contactId]`. `toast.success("Consent recorded")` on success. `toast.error(err.response?.data?.detail)` on failure.

Uses existing token classes: `bg-background`, `border-border`, `text-foreground`, `text-foreground/50`, `bg-primary`, `text-primary-foreground`, `focus:ring-ring`.

---

#### New: `frontend/src/pages/RetentionReport.tsx`

Route: `/settings/biometric`. Guard: `AdminRoute`.

TanStack Query: `queryKey: ["biometric","retention", {withinDays, includePurged, offset}]`, refetch on filter change.

Controls: `within_days` number input (default 30), `include_purged` checkbox.

Mobile layout: stacked cards (one per row). `md:` and up: table with columns Contact, Status, Consented at, Retention until (highlighted in `text-red-600 dark:text-red-400` if expired), Deletion requested, Photos, Actions.

Pagination: offset-based Prev/Next (mirrors `LogsPage.tsx:95-114`). Results capped to max 200 per page.

"Purge now" button per row: `ConfirmDialog` → `biometricService.purgeNow(item.contact_id)` → invalidates `["biometric","retention",...]` + `["biometric","consent",item.contact_id]` + `["contact-faces",item.contact_id]`.

---

#### New: `frontend/src/services/biometric.ts`

```typescript
import api from "./api";
import type {
  ConsentResponse, ConsentRecordRequest, ConsentUpdateRequest,
  DeletionRequest, PurgeResultResponse, RetentionReportResponse,
} from "../types/biometric";

interface RetentionReportParams {
  within_days?: number;
  include_purged?: boolean;
  limit?: number;
  offset?: number;
}

export const biometricService = {
  getConsent: (contactId: number): Promise<ConsentResponse> =>
    api.get(`/biometric/contacts/${contactId}/consent`).then(r => r.data),

  recordConsent: (contactId: number, data: ConsentRecordRequest): Promise<ConsentResponse> =>
    api.post(`/biometric/contacts/${contactId}/consent`, data).then(r => r.data),

  updateConsent: (contactId: number, data: ConsentUpdateRequest): Promise<ConsentResponse> =>
    api.patch(`/biometric/contacts/${contactId}/consent`, data).then(r => r.data),

  revokeConsent: (contactId: number, reason?: string): Promise<ConsentResponse> =>
    api.post(`/biometric/contacts/${contactId}/consent/revoke`, { reason }).then(r => r.data),

  requestDeletion: (contactId: number, data: DeletionRequest): Promise<ConsentResponse | PurgeResultResponse> =>
    api.post(`/biometric/contacts/${contactId}/deletion-request`, data).then(r => r.data),

  purgeNow: (contactId: number): Promise<PurgeResultResponse> =>
    api.post(`/biometric/contacts/${contactId}/purge`).then(r => r.data),

  getRetentionReport: (params: RetentionReportParams): Promise<RetentionReportResponse> =>
    api.get("/biometric/retention/report", { params }).then(r => r.data),
};
```

All calls route through the shared `api.ts` axios instance (inherits the 401→refresh interceptor and the `baseURL: '/api'` dev/prod convention).

---

#### New: `frontend/src/types/biometric.ts`

```typescript
export type ConsentStatus =
  | "none"
  | "consented"
  | "revoked"
  | "deletion_requested"
  | "purged";

export interface ConsentResponse {
  contact_id: number;
  status: ConsentStatus;
  consent_given: boolean | null;
  consented_at: string | null;
  recorded_by_name: string | null;
  basis_note: string | null;
  retention_until: string | null;
  deletion_requested_at: string | null;
  deletion_requested_by_name: string | null;
  purged_at: string | null;
  enrolled_photo_count: number;
  subject_active: boolean;
}

export interface PurgeResultResponse {
  contact_id: number;
  purged_at: string;
  subject_deleted: boolean;
  samples_deleted: number;
  files_deleted: number;
  errors: string[];
}

export interface RetentionReportItem extends ConsentResponse {
  contact_name: string;
}

export interface RetentionReportResponse {
  items: RetentionReportItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ConsentRecordRequest {
  basis_note?: string;
  retention_years?: number;
}

export interface ConsentUpdateRequest {
  basis_note?: string;
  retention_until?: string;  // ISO datetime string; admin-only meaningful
}

export interface DeletionRequest {
  reason?: string;
  immediate?: boolean;
}
```

### 5.2 TanStack Query keys and Zustand

| Key | Endpoint | Invalidated by |
|---|---|---|
| `["biometric","consent",contactId]` | GET `/biometric/contacts/{id}/consent` | record, update, revoke, deletion-request, purge |
| `["biometric","retention",{...params}]` | GET `/biometric/retention/report` | purge (from RetentionReport row) |
| `["contact-faces",contactId]` | GET `/contacts/{id}/faces` (S07) | **also** invalidated by purge (enrolled thumbnails must clear) |

No new Zustand store. Role gating reads `useAuthStore((s) => s.isAdmin)` from `frontend/src/store/authStore.ts` (Zustand), plus deriving `isVolunteer = role === "volunteer" || role === "admin"`.

Role gating summary:
- `viewer` → all mutation controls not rendered; GET returns 200.
- `volunteer` → record / edit-note / revoke / request-deletion visible; retention-years field read-only; "Purge now" absent.
- `admin` → all controls including "Purge now", editable retention-years, `/settings/biometric` nav.

### 5.3 UX states and design tokens

- **Loading**: `LoadingState` from `components/ui/StateViews.tsx`.
- **Empty (`status === "none"`)**:  `EmptyState` with "No biometric consent on file" + "Record consent" CTA (volunteer+). Viewer sees status pill only.
- **Error**: `ErrorState` with retry. All mutation errors via `toast.error(err.response?.data?.detail ?? "Unexpected error")`.
- **Post-purge**: status pill switches to "Face data purged" (red). `enrolled_photo_count` shows 0. S07 `["contact-faces",contactId]` invalidated so FacePanel thumbnails clear. `toast.success("Face data purged for {contactName}")`.
- **409 re-consent after purge**: `toast.error("Face data was purged for this contact. Re-enrollment requires admin review.")`.

Design tokens — **only use Tailwind token classes; never hardcoded hex:**
- `bg-card`, `text-foreground`, `text-foreground/50`, `border-border`, `bg-background`, `bg-primary`, `text-primary-foreground`, `focus:ring-ring`.
- Status colors: `text-green-600 dark:text-green-400`, `text-amber-600 dark:text-amber-400`, `text-orange-600 dark:text-orange-400`, `text-red-600 dark:text-red-400` — these are Tailwind functional-color classes that resolve correctly under `.dark` class-based dark mode (from `main.tsx:9-14`). `bg-amber-50 dark:bg-amber-900/20` for the warning banner is acceptable per the same precedent used in S07's `FacePanel`.

Mobile-first: `ConsentPanel` is a full-width card at 375px. Actions wrap or collapse under a `...` overflow menu on very narrow screens. `RetentionReport` uses stacked cards on mobile, `md:table` layout on desktop. All buttons ≥44px touch target (`min-h-[44px]`).

### 5.4 File-by-file change list

**CREATE:**
- `frontend/src/components/biometric/ConsentPanel.tsx`
- `frontend/src/components/biometric/RecordConsentDialog.tsx`
- `frontend/src/pages/RetentionReport.tsx`
- `frontend/src/services/biometric.ts`
- `frontend/src/types/biometric.ts`
- `frontend/src/components/biometric/ConsentPanel.test.tsx`
- `frontend/src/pages/RetentionReport.test.tsx`

**MODIFY:**
- S03 Contact Detail page (`frontend/src/pages/ContactDetail.tsx` or equivalent — coordinate with S03) — mount `<ConsentPanel contactId={id} contactName={fullName} />` below the S07 `<FacePanel>` in the face/privacy section.
- `frontend/src/App.tsx` — add route:
  ```tsx
  <Route
    path="/settings/biometric"
    element={<AdminRoute><RetentionReport /></AdminRoute>}
  />
  ```
- `frontend/src/components/layout/BottomNav.tsx` — add "Biometric & Privacy" link under the admin "More" sheet, visible only when `isAdmin`.

---

## 6. Migration / data

**Schema migration:** purely additive — new table, new column on `compreface_subjects`. Zero downtime when applied. Existing rows are unaffected.

**Operational backfill (post-deploy, owner-decided):**

If the owner wants explicit `consent_given=false` rows for all currently-enrolled contacts (so staff see the amber warning banner for each), run this SQL after confirming with the owner:

```sql
INSERT INTO biometric_consent (contact_id, consent_given, created_at, updated_at)
SELECT DISTINCT cs.contact_id,
       false,
       CURRENT_TIMESTAMP,
       CURRENT_TIMESTAMP
FROM   compreface_subjects cs
WHERE  cs.enrollment_status = 'active'
  AND  cs.contact_id IS NOT NULL
  AND  NOT EXISTS (
           SELECT 1 FROM biometric_consent bc
           WHERE bc.contact_id = cs.contact_id
       );
```

Without this script the amber warning banner fires correctly anyway (it checks `subject_active && status === "none"` where "none" = no row exists).

**Retention default:** `biometric_retention_years=7` seeded at setup. Changing this value later does **not** retroactively recompute existing `retention_until` values — those are snapshotted at consent-record time and are immutable. Document this in the admin_settings `description` field and in the UI tooltip.

---

## 7. Acceptance criteria

1. `POST /biometric/contacts/{id}/consent` creates a row with `consent_given=true`, `consented_at ≈ utc_now()`, `recorded_by_id = caller.id`, `retention_until ≈ consented_at + 7*365 days`; response `status="consented"`. `audit_log` receives one row with `action="consent_recorded"`, `before=None`.
2. Re-POSTing the same contact **updates** (upserts) the existing row; no second row is created; `uq_biometric_consent_contact` prevents a raw duplicate insert.
3. A volunteer `PATCH`-ing `retention_until` receives **403**; an admin succeeds; `audit_log` receives `action="consent_updated"`.
4. A viewer token receives **403** on all mutations (POST record, PATCH, revoke, deletion-request, purge); receives **200** on GET consent.
5. `POST /deletion-request` with `immediate=false` sets `deletion_requested_at`/`deletion_requested_by_id`; response `status="deletion_requested"`. Returns **404** when contact has no subject, no `face_samples`, and no consent row.
6. `POST /deletion-request` with `immediate=true` called by admin triggers synchronous purge; response has `purged_at` set; `status="purged"`.
7. `POST /purge` (admin) for a contact with one active subject and 2 `face_samples`:
   - Calls `ComprefaceClient.delete_subject(compreface_subject_id)` exactly once.
   - Deletes both `face_samples` rows from the DB.
   - Deletes the corresponding files from disk (verified via `tmp_path` fixture).
   - Sets `compreface_subjects.enrollment_status="purged"`, nulls `compreface_subject_id`, `sample_count=0`.
   - Sets `biometric_consent.purged_at ≈ utc_now()`, `purge_detail.samples_deleted=2`.
   - Returns `PurgeResultResponse` with correct counts.
8. After purge, the `contacts` row is untouched (`is_deleted` unchanged). Any `participants` rows for that contact are preserved.
9. Every purge writes exactly one `audit_log` row with `action="biometric_purged"`, `entity="biometric_consent"`, `entity_id=consent.id`, non-null `before` and `after`.
10. Re-running `POST /purge` on an already-purged contact (`subject_deleted=True`) is idempotent: returns 200 with the same stored `purge_detail`; `delete_subject` is **not** called again; no new audit row written.
11. If `delete_subject` raises (CompreFace unreachable): file + DB erasure still complete; `purged_at` is set; `subject_deleted=false`; error message in `purge_detail.errors`. A subsequent `POST /purge` retries the CompreFace delete only (local erasure is skipped due to idempotency).
12. The daily `_process_biometric_retention` job (called via `run()`):
    - Purges a consent whose `retention_until < now`.
    - Purges a consent with `deletion_requested_at IS NOT NULL`.
    - Skips rows where `purged_at IS NOT NULL`.
    - System-actor purges write `audit_log.actor_id=NULL`.
    - Two rapid invocations within 24 h → `delete_subject` called at most once (24 h guard).
13. `GET /biometric/retention/report` (admin) returns consents expiring within `within_days` and/or with pending deletion requests; paginated (clamped to max 200); sorted `retention_until ASC NULLS LAST`; excludes purged unless `include_purged=true`.
14. `POST /biometric/contacts/{id}/consent` returns **409** after `purged_at` is already set.
15. Frontend `ConsentPanel`:
    - Amber warning banner shows when `subject_active=true && status="none"`.
    - All mutation controls are role-gated (viewer sees none).
    - "Purge now" button (admin) triggers `ConfirmDialog`; on confirm → `POST /purge`; success toast fires; `enrolled_photo_count` shows 0; S07 `["contact-faces",id]` query is invalidated.
    - All mutation errors surface via `toast.error(err.response?.data?.detail)`.
16. `ruff check app` clean; `npm run build` passes with no TypeScript errors; `npm run lint` clean; full backend `pytest tests/ -q` green (including pre-existing tests — `_delete_image_files` refactor in `face_cleanup.py` must not break existing `FaceCleanupService` behavior).

---

## 8. Test plan

### Backend pytest

Run environment: `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`

Mock `ComprefaceClient` per AGENTS.md: `unittest.mock.AsyncMock` on `delete_subject`. Use `pytest`'s `tmp_path` fixture for `storage_base` so file-deletion assertions use real files (not mocks). Auth headers via `conftest.py`'s `make_token` / `admin_auth_headers` / `volunteer_auth_headers` fixtures.

**`backend/tests/test_biometric_consent.py`**

| Test | Key assertion |
|---|---|
| `test_record_consent_creates_row_with_correct_retention` | `retention_until ≈ consented_at + 7*365 days`; `audit_log` has 1 row with `action="consent_recorded"`, `before=None` |
| `test_record_consent_upsert_no_duplicate` | Second POST returns 200 and updates; raw SQL `SELECT COUNT(*) FROM biometric_consent WHERE contact_id=?` returns 1 |
| `test_record_consent_409_after_purge` | Consent row with `purged_at` set → 409 |
| `test_volunteer_cannot_patch_retention_until` | PATCH `{"retention_until": "2033-01-01T00:00:00"}` with volunteer JWT → 403 |
| `test_admin_can_patch_retention_until` | PATCH with admin JWT → 200; `audit_log` has `action="consent_updated"` |
| `test_viewer_read_only` | GET → 200; POST record → 403; POST revoke → 403; POST purge → 403 |
| `test_deletion_request_sets_fields` | `deletion_requested_at` set, `status=="deletion_requested"` in response |
| `test_deletion_request_404_no_biometric_data` | Contact with no subject/samples/consent → 404 |
| `test_deletion_request_immediate_admin_purges_synchronously` | `immediate=true`, admin → `purged_at` in response, `status=="purged"` |
| `test_revoke_sets_consent_given_false` | `consent_given=false`; `audit_log` has `action="consent_revoked"` |
| `test_retention_report_filters_and_paginates` | Seed 3 consents (2 expiring in 10 days, 1 with deletion_requested, 1 purged); `within_days=15` → returns 3 (not purged); `include_purged=true` → 4 |
| `test_get_consent_returns_none_status_when_no_row` | GET for contact with no consent row → 200, `status="none"`, no 404 |
| `test_get_consent_includes_derived_enrolled_photo_count` | Contact with 2 `face_samples` rows → `enrolled_photo_count=2` |

**`backend/tests/test_biometric_purge.py`**

| Test | Key assertion |
|---|---|
| `test_purge_deletes_subject_samples_and_files` | tmp storage with 2 real files; 2 `face_samples`; assert: files unlinked via `os.path.exists` checks, rows deleted, `delete_subject` called once, `purged_at` set, `purge_detail.samples_deleted=2` |
| `test_purge_preserves_contact_and_participants` | Contact row `is_deleted` unchanged; `participants` row for contact still exists |
| `test_purge_writes_audit_log_biometric_purged` | Exactly one `audit_log` row with `action="biometric_purged"`, `entity="biometric_consent"`, non-null `before` and `after` |
| `test_purge_idempotent_full_success` | Second `POST /purge` call → 200 with same `purge_detail`; `delete_subject.call_count` remains 1 |
| `test_purge_retries_compreface_on_partial_failure` | First purge: `delete_subject` raises `httpx.ConnectError`; `purged_at` set, `subject_deleted=False`. Second purge: `delete_subject` called again; `subject_deleted=True`, `purge_detail` updated |
| `test_purge_tolerates_compreface_error_files_still_deleted` | `delete_subject` raises; files unlinked, `face_samples` rows deleted, `purged_at` set, `purge_detail.errors` non-empty |
| `test_purge_contact_no_subject_only_samples` | No `compreface_subjects` row; `face_samples` rows and files deleted; no `delete_subject` call |
| `test_purge_contact_no_biometric_data` | No subject, no samples, no consent row; `purge_contact` → `samples_deleted=0`, `files_deleted=0`, `purged_at` set (to block future enrollment) |
| `test_purge_anonymizes_linked_detection_crops` | `Detection.is_enrolled=True` with `compreface_subject_id` matching the subject; after purge `detection.image_path=None`, `detection.deleted_at` set, file unlinked |
| `test_retention_job_purges_expired_consent` | Seed consent with `retention_until = utc_now() - timedelta(days=1)`; call `_process_biometric_retention`; `purged_at` set; `audit_log.actor_id=None` |
| `test_retention_job_purges_deletion_requested` | `deletion_requested_at` set; job purges it |
| `test_retention_job_skips_already_purged` | `purged_at` already set; `delete_subject` not called |
| `test_retention_job_24h_guard` | Two rapid calls within seconds; `delete_subject.call_count == 1` (second call returns `False` without running) |
| `test_face_cleanup_helper_refactored_correctly` | Call `_delete_image_files(tmp_path, "faces/test_thumb.jpg")` with real file at that path; file deleted, return `(1, [])`. Call with `None` path → `(0, [])`. |

### Frontend vitest (`npm run test:run`)

**`frontend/src/components/biometric/ConsentPanel.test.tsx`**

- `renders_status_pill_for_each_status_value` — mock `biometricService.getConsent` returning each of the 5 status values; assert pill text.
- `shows_amber_banner_when_subject_active_and_status_none` — mock `subject_active=true, status="none"`; assert warning banner DOM node.
- `hides_all_mutation_controls_for_viewer_role` — mock authStore role="viewer"; assert "Record consent", "Revoke", "Request deletion", "Purge now" not in DOM.
- `shows_purge_only_for_admin` — volunteer: "Purge now" absent; admin: present.
- `purge_opens_confirm_dialog_then_calls_mutation` — click "Purge now" → `ConfirmDialog` appears; click confirm → `biometricService.purgeNow(1)` called; `queryClient.invalidateQueries(["biometric","consent",1])` and `queryClient.invalidateQueries(["contact-faces",1])` called.
- `mutation_error_shows_sonner_toast_with_server_detail` — mock purge to throw axios error with `response.data.detail = "Test error"`; assert `toast.error` called with "Test error".
- `record_consent_opens_dialog_and_posts_on_submit` — click "Record consent" → dialog opens; fill basis_note; submit → `biometricService.recordConsent(1, {...})` called; `toast.success` on success.

**`frontend/src/pages/RetentionReport.test.tsx`**

- `renders_table_rows_and_pagination` — mock `getRetentionReport` returning 2 items, `total=15`; assert 2 rendered rows; "Next" button present.
- `purge_from_report_row_opens_confirm_then_invalidates_cache` — click "Purge now" on row 1; confirm; assert `biometricService.purgeNow(item.contact_id)` called; `["biometric","retention",...]` and `["contact-faces", item.contact_id]` queries invalidated.

---

## 9. Rollout / rollback / risks

### Rollout sequence

1. Apply `alembic upgrade head` (additive — no downtime required; can run on live Unraid instance).
2. Deploy backend + frontend as a single Compose update (`docker compose up -d`).
3. The `biometric_retention_years=7` default is seeded at setup; existing installs need a one-time admin_settings row insert or via the settings endpoint.
4. The retention job is dormant until a consent row's `retention_until` is in the past — no immediate mass action on deploy (new consents get 7-year horizon; no currently enrolled faces have had any S08 consent record at all yet).
5. After deploy: admin visits `/settings/biometric` (Retention Report) to see enrolled contacts with no consent row (indicated by empty report — the ConsentPanel amber banner is the per-contact view; the report only surfaces rows that exist).
6. Optional: run the backfill SQL (§6) to create `consent_given=false` rows for all enrolled contacts so staff can systematically collect consent via the ConsentPanel.

### Rollback

- **Code rollback** (redeploy prior image): safe — S08 endpoints disappear but the schema persists. No data corruption.
- **Migration downgrade**: `alembic downgrade -1` drops `biometric_consent` table (all consent records permanently lost) and `compreface_subjects.consent_id`. **Treat as irreversible after go-live.** The migration docstring must say this loudly. Purged biometric data cannot be recovered regardless.
- **Already-purged data**: RTBF is permanent by design. No recovery path exists. This is the legal guarantee.

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| CompreFace unavailable during purge — subject template persists in CompreFace | File + DB erasure proceeds regardless; `subject_deleted=false` recorded; local `compreface_subject_id` nulled prevents recognition re-match; daily job retries CompreFace delete; `POST /enrollment/backfill` reports the orphaned CompreFace subject as `remote_only` for manual cleanup |
| Admin accidentally purges the wrong contact | Double `ConfirmDialog` with contact name in copy; `audit_log` records actor and timestamp; biometrics-only (contact + attendance preserved); no undo — by design |
| Race: two parallel purge requests | `with_for_update()` on consent row; second request finds `purged_at` set in Step 0 and returns idempotently; no double CompreFace call |
| `_process_biometric_retention` over-running (large backlog) | Batch limit 100 per cycle; 24 h guard; each purge per-contact (small); if backlog exists it will be processed over multiple days |
| `_delete_image_files` refactor breaks existing `FaceCleanupService` | Pure internal refactor of `_process_detection` (`face_cleanup.py:110-131`); existing tests must pass; the helper has identical semantics (`deleted_any` logic → `(count, errors)` tuple) |
| S07 not yet landed when S08 is implemented | `face_samples` table FK target does not exist; migration will fail. Hard dependency — verify S07 migration is applied first |
| Retention default change silently affecting existing consents | Retention snapshotted at record time; immutable; doc in admin_settings description and UI |

---

## 10. Open questions & pending owner artifacts

1. **Default retention period**: spec uses 7 years. Owner must confirm the church's internal policy; Philippine RA 10173 does not specify a fixed biometric retention period for religious organizations.

2. **Hard enrollment gate** — RESOLVED (2026-06-22): **soft gate** at go-live. S08 ships the warning banner only; `enroll_without_consent` defaults effectively permissive at cutover (S24 sets it `true` so recognition keeps working while staff collect consent over time). The owner flips to a hard gate later once consent coverage is sufficient — the one-line change (raise 403 in `EnrollmentService.enroll_contact_face` when `consent is None or not consent.consent.consent_given`) is marked by the `# TODO(S08-policy)` comment in `enrollment.py`. Retention period (item 1) is still owner-pending.

3. **Consent collection medium**: verbal (note in `basis_note`), signed paper form (reference number in `basis_note`), or future digital signature? If digital signature is needed, add a `consent_document_path` column in a future sprint.

4. **Revoke semantics**: revoke ≠ auto-purge per this spec (confirmed by decisions.md pattern). If the owner later wants revoke to immediately queue a deletion, set `deletion_requested_at = utc_now()` inside `BiometricConsentService.revoke` — zero other changes required.

5. **Backfill**: does the owner want `consent_given=false` rows created for all currently-enrolled contacts (so staff see the amber banner on every profile)? The backfill SQL in §6 is ready. Owner to confirm before running in production.

6. **S16 integration**: if S16 (APScheduler + `job_runs` health tracking) ships before S08 is implemented, replace the `_last_retention_run` in-memory guard with an APScheduler job that logs to `job_runs`. No functional change — pure observability improvement.

---

## Cross-sprint dependencies, shared-model touchpoints & inconsistencies for the master doc

- **Hard dependency chain**: S01 → S07 → S08. S01 provides `contacts` table (FK target for `biometric_consent.contact_id`), `audit_log` table (written by S08), and the role enum `admin|volunteer|viewer`. S07 provides `face_samples` table (deleted by purge), `compreface_subjects` columns `purged_at`/`enrollment_source`/`last_trained_at`/`is_orphan` (S08 adds `consent_id` to the same table), and `EnrollmentService.purge_subject_local` seam. The master migration chain must be: S01 head → S07 head → S08. Do not start S08 migration until S07 is applied.

- **`compreface_subjects` co-ownership (S07 + S08)**: S07 migration adds `enrollment_source`, `last_trained_at`, `is_orphan`, `purged_at`. S08 migration adds `consent_id`. The two migrations must not collide. Assign: S07 migration is `down_revision` for S08 migration. Master doc must enforce this ordering.

- **`face_samples` deletion ownership**: S07 `purge_subject_local` deletes `face_samples` rows locally. S08 `BiometricPurgeService.purge_contact` **also** deletes them (`DELETE ... WHERE contact_id = ?`). These are not in conflict because S08 **replaces** the role of `purge_subject_local` for the RTBF path — `purge_contact` does the complete deletion directly rather than calling `purge_subject_local`. Master doc must note: for RTBF, use S08's `BiometricPurgeService.purge_contact`; for non-RTBF local removal (e.g., sample-level removal in `EnrollmentService.remove_sample`), use S07's `remove_sample`. Do **not** call both.

- **`_delete_image_files` helper ownership**: extracted from `FaceCleanupService._process_detection` (`face_cleanup.py:110-131`) by S08. S07's `EnrollmentService.remove_sample` also deletes files — it should import this helper once it exists. Coordinate: if S07 implements first, it writes its own local `_delete_files` inline; S08 extracts and merges. If S08 first, S07 imports from `face_cleanup`. Master doc should note the desired final state: one helper, both services import from `face_cleanup`.

- **S11 (merge) touch**: `EnrollmentService.reassign_subject_contact` (S07) repoints `compreface_subjects.contact_id` and `face_samples.contact_id` from the loser to the survivor in a merge. The `biometric_consent` row (keyed on `contact_id` UNIQUE) must also be reassigned or merged. S11's merge transaction must include: if the loser has a consent row and the survivor does not → reassign `biometric_consent.contact_id = survivor.id`; if both have consent rows → keep the survivor's and delete the loser's. The master doc must assign this to S11.

- **`compreface_subject_id` nulled after purge prevents re-recognition**: the live pipeline's `_find_member_id` (`face_pipeline.py:26–33`) queries `ComprefaceSubject WHERE compreface_subject_id == subject_id`. After purge, this column is `NULL` on the row. The purged contact is therefore invisible to the recognition pipeline — future face captures of this person will produce `unknown` detections in the PIT queue. This is the correct intended behavior. Master doc should document it as a known invariant.

- **`audit_log` S01 dependency**: if S01 does not include `audit_log` in its migration, the S08 implementer must create it before S08. The master doc should assign `audit_log` creation explicitly to S01 to prevent duplicate creation.

- **S16 APScheduler**: `_process_biometric_retention` is written as a `QueueManager` method with a `_last_retention_run` guard (same pattern as the existing `_last_cleanup_run` guard). S16 will wrap it into a proper APScheduler job with `job_runs` logging. The method signature and algorithm do not need to change for that transition.
