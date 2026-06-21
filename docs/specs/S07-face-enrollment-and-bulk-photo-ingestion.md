# S07 — Face Enrollment & Bulk Photo Ingestion

**Phase:** C — Face-native (PRIORITY) · **Depends on:** S01, S03, S04 · **Effort:** XL · **Status:** Not started

> **Name-space contract (from S01):** All symbols in this spec use the post-S01 canonical names: `Contact` (was `CiviCRMMember`, table `contacts`), `Event` (was `CiviCRMEvent`, table `events`), `Participant` (was `Attendance`, table `participants`), app-minted `id` PKs with nullable `external_id`. `compreface_subjects.contact_id` FKs to `contacts.id`. Where a legacy symbol still exists in the codebase at implementation time (S01 may be in-flight), the old name appears in parentheses for cross-reference only.

---

## 1. Goal & rationale

Today the app has **no code path that ever creates a `ComprefaceSubject` row or sets its `contact_id`** — the subject-to-contact link that turns a recognized face into a participant record is seeded entirely outside the visible codebase (DB seed / external script). This is the "enrollment gap" documented in `docs/crm-research/backend.md` §(C):

- `queue_manager._process_enrollment` (`backend/app/services/queue_manager.py:163`) only flips an *already-existing* pending subject to `active` and uploads one sample image; it never creates the subject or the contact link.
- `pit.enroll_pit_task` (`backend/app/routers/pit.py:54`) stamps `detection.matched_name = "member:{contact_id}"` and `detection.is_enrolled = True` but never calls `add_subject`/`add_example` and never creates a `ComprefaceSubject`.
- `task_service.add_task` (`backend/app/services/task_service.py:202`) does the same stamping — no subject row, no CompreFace call.
- `ComprefaceClient` already exposes `add_subject` (`compreface.py:151`) and `add_example` (`compreface.py:157`); the orchestration layer is simply absent.

S07 closes that gap and makes Seraphim truly face-recognition-native across five deliverables:

1. **First-class enroll endpoint** — atomically creates `ComprefaceSubject(contact_id=...)` + a `face_samples` row + calls CompreFace `add_subject`/`add_example`, with full rollback on partial failure.
2. **Bulk historical-photo ingestion** — reuses the existing `uploads.py` / `process_face_crop` / `task_service.py` / `pit.py` pipeline so the owner's back-catalog of event photos bootstraps the face database: upload → detect → recognize → PIT/review queue → volunteer confirms identity → auto-enroll the confirmed crop.
3. **Auto-enroll from future confirmed detections** — when a volunteer confirms/edits/adds an identity to an unrecognized face whose subject is not yet active, the confirmed crop is enrolled automatically (decisions.md Round 2(b)).
4. **Backfill admin job** — reconciles existing `compreface_subjects` rows against CompreFace's live subject list and against `contacts`, marking orphans and seeding `face_samples` rows from on-disk crops.
5. **Contact-profile face panel** — enrolled photos, add/replace/remove per-sample, re-train (rebuild from local samples), and recognition attendance history; mounted in the slot reserved by S03.

**Design principle:** reuse, do not rewrite, the proven pipeline (`process_face_crop`, `FaceQualityGate`, `FaceStorage`, `DedupCache`, `ComprefaceClient`, `task_service`, `pit`). Net-new code is bounded to: `EnrollmentService`, `face_samples`/`photo_ingest_batches` tables, additive columns on `compreface_subjects`, bulk-ingest batch tracking, the backfill admin job, and the profile face panel + bulk upload UI.

---

## 2. Scope

### In scope

- New table `face_samples` (per enrolled image with `compreface_image_id` for targeted delete from CompreFace).
- New table `photo_ingest_batches` (per-batch bookkeeping and per-image result report for bulk upload).
- Four additive columns on `compreface_subjects` (`enrollment_source`, `last_trained_at`, `is_orphan`, `purged_at`).
- `EnrollmentService` (`backend/app/services/enrollment.py`, new file) with methods: `enroll_contact_face`, `add_sample`, `remove_sample`, `retrain_subject`, `reassign_subject_contact` (seam for S11 merge), `purge_subject_local` (seam for S08 RTBF; S07 defines the method signature, S08 fills the destructive logic).
- New router `backend/app/routers/enrollment.py` (paths: `/contacts/{id}/faces`, `/contacts/{id}/faces/{sample_id}`, `/contacts/{id}/faces/retrain`, `/contacts/{id}/recognition-history`, `/enrollment/backfill`).
- Bulk-ingest endpoints added to `backend/app/routers/uploads.py` (`POST /uploads/photos/batch`, `GET /uploads/photos/batch/{batch_id}`).
- `ComprefaceClient` extension: `add_example` modified to return `Optional[str]` image_id; new `delete_example(image_id: str) -> bool`.
- Auto-enroll wiring: `task_service.py` resolve paths (confirm/edit/add reaching `required_approvals`) and `pit.enroll_pit_task` both call `EnrollmentService.enroll_contact_face` on resolution.
- Refactor `queue_manager._process_enrollment` to delegate to `EnrollmentService` (removes duplication).
- Frontend: `FacePanel` component (`frontend/src/components/contacts/FacePanel.tsx`), `BulkPhotoUploadPage` (`frontend/src/pages/BulkPhotoUploadPage.tsx`), `PitPage` "Enroll" wired to contact picker, contact detail face panel slot wired.
- Alembic migration (one file), backend pytest suite (`tests/test_enrollment.py`, `tests/test_photo_ingest.py`), frontend vitest, `npm run build` and `npm run lint` passing.

### Out of scope

- Biometric consent capture, retention policy, right-to-be-forgotten purge of CompreFace and stored crops → **S08**. S07 stores the `purged_at` column and gates enrollment on it; S08 owns the destructive purge and the `biometric_consent` table.
- Face clustering of unknown faces into auto-suggested identity groups — decisions.md Round 2 specifies unknowns stay in PIT until individually identified; no auto-clustering.
- Recognition-health dashboards, per-subject accuracy metrics, and subject-relink tooling — explicitly deferred in decisions.md Round 2.
- Find-and-merge duplicate contacts (S11). S07 exposes `reassign_subject_contact` as the shared service seam S11 will call.
- Camera and RTSP worker changes — live pipeline (`rtsp_capture.py`, `face_pipeline.py`) is untouched. `_find_member_id` (`face_pipeline.py:26`) already resolves subjects via `compreface_subjects.contact_id`.
- CiviCRM removal — S01 scope. This spec assumes S01 has landed; all table/model names are post-S01.
- Report photos submitted via community-report forms — those are name-list attendance-intake photos (S22), not face-enrollment photos.

---

## 3. Data model changes

### 3.1 New table `face_samples`

Per-image record of a face sample enrolled under a CompreFace subject. One contact may have many samples; more samples improve recognition accuracy.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK identity | no | — | app-minted |
| `contact_id` | Integer FK → `contacts.id` ON DELETE CASCADE | no | — | indexed |
| `subject_id` | Integer FK → `compreface_subjects.id` ON DELETE CASCADE | no | — | indexed |
| `image_path` | Text | no | — | bare relative path to full-resolution crop (e.g. `enrolled/contact_42/sample_001_full.jpg`); served via authenticated `/storage/{path}` route only |
| `thumb_path` | Text | yes | NULL | thumbnail 300×300 |
| `source` | String(20) | no | server_default `'manual'` | `manual` \| `detection` \| `bulk_ingest` \| `backfill` |
| `quality` | Numeric(5,3) | yes | NULL | blur-variance from `FaceQualityGate` or detection confidence |
| `detection_id` | Integer FK → `detections.id` ON DELETE SET NULL | yes | NULL | provenance when enrolled from a confirmed detection |
| `compreface_image_id` | String(255) | yes | NULL | image_id returned by CompreFace `add_example`; used for targeted per-sample delete via `DELETE /api/v1/recognition/faces/{image_id}` |
| `added_by_id` | Integer FK → `users.id` ON DELETE SET NULL | yes | NULL | actor user |
| `created_at` | DateTime naive UTC | no | `utc_now` | |

Indexes: `ix_face_samples_contact_id` on `(contact_id)`, `ix_face_samples_subject_id` on `(subject_id)`. No unique constraint — multiple samples per contact are expected and intentional.

### 3.2 New table `photo_ingest_batches`

Tracks progress and per-image results for a bulk-upload job. The UI polls `GET /uploads/photos/batch/{batch_id}` to show live progress without a long-lived connection.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK identity | no | — | batch id the UI polls |
| `event_id` | Integer FK → `events.id` ON DELETE SET NULL | yes | NULL | optional; passed as `event_id` to `process_face_crop` for every face in the batch |
| `uploaded_by_id` | Integer FK → `users.id` ON DELETE SET NULL | yes | NULL | |
| `status` | String(20) | no | server_default `'processing'` | `processing` \| `completed` \| `failed` |
| `total_images` | Integer | no | server_default `'0'` | set at creation from `len(files)` |
| `processed_images` | Integer | no | server_default `'0'` | incremented per file on completion |
| `faces_detected` | Integer | no | server_default `'0'` | cumulative sum |
| `auto_logged` | Integer | no | server_default `'0'` | tier-100 faces → Participant written |
| `tasks_created` | Integer | no | server_default `'0'` | faces sent to volunteer/PIT queue |
| `skipped` | Integer | no | server_default `'0'` | quality-gate failures |
| `deduplicated` | Integer | no | server_default `'0'` | |
| `errors` | Integer | no | server_default `'0'` | per-image decode/detect errors |
| `report` | JSONB | no | server_default `'[]'` | per-image rows: `[{filename, faces_detected, outcome, error?}]`; capped at 500 entries in-process |
| `created_at` | DateTime naive UTC | no | `utc_now` | |
| `finished_at` | DateTime | yes | NULL | set on completion or failure |

Index: `ix_photo_ingest_batches_status` on `(status)`.

`report` column: `JSON().with_variant(JSONB, "postgresql")` using the module-level `JSONB` alias from `models.py:22`.

### 3.3 Additive columns on `compreface_subjects` (existing model: `models.py:87`)

The existing model already has `enrollment_status` (`pending`|`active`) at `models.py:96` and `sample_count` at `models.py:99`. Four additive columns:

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `enrollment_source` | String(20) | yes | NULL | `manual` \| `bulk_ingest` \| `detection` \| `backfill`; set only on first enrollment, never overwritten |
| `last_trained_at` | DateTime | yes | NULL | updated on every successful `add_example` or retrain |
| `is_orphan` | Boolean | no | server_default `'false'` | set true by backfill when CompreFace has no matching subject or `contact_id` points to a deleted/absent contact |
| `purged_at` | DateTime | yes | NULL | set by S08 RTBF; when non-null all enroll/add/retrain calls on this subject return 409 |

**`sample_count` maintenance change:** S07 changes the maintenance rule from blind `+= 1` (the current `queue_manager.py:221` pattern, which drifts) to a recomputed `SELECT COUNT(*) FROM face_samples WHERE subject_id = $1` after every enroll/remove/retrain. The column value is always correct at rest.

### 3.4 Alembic migration plan

**One migration file** for S07, e.g. `s07_face_enrollment_and_samples`. `down_revision` = last S04 migration head. Verify at implementation time; never edit an applied migration (AGENTS.md).

**Forward (`upgrade`):**

```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

# Use same cross-dialect JSONB pattern as models.py:22
JSONB_col = sa.JSON().with_variant(pg.JSONB(none_as_null=True), "postgresql")

# 1. face_samples
op.create_table(
    "face_samples",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("contact_id", sa.Integer(),
              sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    sa.Column("subject_id", sa.Integer(),
              sa.ForeignKey("compreface_subjects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("image_path", sa.Text(), nullable=False),
    sa.Column("thumb_path", sa.Text(), nullable=True),
    sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
    sa.Column("quality", sa.Numeric(5, 3), nullable=True),
    sa.Column("detection_id", sa.Integer(),
              sa.ForeignKey("detections.id", ondelete="SET NULL"), nullable=True),
    sa.Column("compreface_image_id", sa.String(255), nullable=True),
    sa.Column("added_by_id", sa.Integer(),
              sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False,
              server_default=sa.text("(datetime('now'))")),
)
op.create_index("ix_face_samples_contact_id", "face_samples", ["contact_id"])
op.create_index("ix_face_samples_subject_id", "face_samples", ["subject_id"])

# 2. photo_ingest_batches
op.create_table(
    "photo_ingest_batches",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("event_id", sa.Integer(),
              sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
    sa.Column("uploaded_by_id", sa.Integer(),
              sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("status", sa.String(20), nullable=False, server_default="processing"),
    sa.Column("total_images", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("processed_images", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("faces_detected", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("auto_logged", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("tasks_created", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("deduplicated", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("report", JSONB_col, nullable=False, server_default="[]"),
    sa.Column("created_at", sa.DateTime(), nullable=False,
              server_default=sa.text("(datetime('now'))")),
    sa.Column("finished_at", sa.DateTime(), nullable=True),
)
op.create_index("ix_photo_ingest_batches_status", "photo_ingest_batches", ["status"])

# 3. Additive columns on compreface_subjects
op.add_column("compreface_subjects",
    sa.Column("enrollment_source", sa.String(20), nullable=True))
op.add_column("compreface_subjects",
    sa.Column("last_trained_at", sa.DateTime(), nullable=True))
op.add_column("compreface_subjects",
    sa.Column("is_orphan", sa.Boolean(), nullable=False, server_default=sa.text("false")))
op.add_column("compreface_subjects",
    sa.Column("purged_at", sa.DateTime(), nullable=True))
```

**Downgrade:**
```python
# Reverse order
op.drop_column("compreface_subjects", "purged_at")
op.drop_column("compreface_subjects", "is_orphan")
op.drop_column("compreface_subjects", "last_trained_at")
op.drop_column("compreface_subjects", "enrollment_source")
op.drop_index("ix_photo_ingest_batches_status", table_name="photo_ingest_batches")
op.drop_table("photo_ingest_batches")
op.drop_index("ix_face_samples_subject_id", table_name="face_samples")
op.drop_index("ix_face_samples_contact_id", table_name="face_samples")
op.drop_table("face_samples")
```

Downgrade is data-lossy by design; dev/rollback only.

**No data backfill in migration.** New columns are nullable or have safe server defaults. The operational backfill of `face_samples` from on-disk crops and CompreFace live list is a runtime admin job (`POST /enrollment/backfill`), not a migration step, because it makes external HTTP calls.

---

## 4. Backend

### 4.1 Endpoint table

All paths registered without `/api` prefix (nginx strips it, CLAUDE.md convention). Auth deps from `backend/app/dependencies.py`: `get_current_user` (any authenticated role), `require_volunteer` (admin + volunteer), `require_admin` (admin only).

| METHOD | Path | Role | Request | Response | Notes |
|---|---|---|---|---|---|
| POST | `/contacts/{contact_id}/faces` | volunteer | multipart `file` (UploadFile) **or** body `detection_id` (int); exactly one required | `FaceSampleResponse` | First-class enroll. Atomic. Creates `ComprefaceSubject` if absent. Quality gate. CompreFace `add_subject` + `add_example`. Writes `face_samples` row. 409 if `purged_at` set. 422 if quality fails. 502 if CompreFace call fails. |
| GET | `/contacts/{contact_id}/faces` | any auth | — | `FacePanelResponse` | Enrolled samples with `/storage/`-prefixed URLs, subject status, `sample_count`, `last_trained_at`, `is_orphan`, `purged_at` stub. |
| DELETE | `/contacts/{contact_id}/faces/{sample_id}` | volunteer | — | `FacePanelResponse` | Remove one sample. Best-effort CompreFace `delete_example` by `compreface_image_id`. Recompute `sample_count`. 404 if sample not found or not owned by this contact. |
| POST | `/contacts/{contact_id}/faces/retrain` | volunteer | — | `FacePanelResponse` | Re-push all local `face_samples` images to CompreFace for this subject. Updates `compreface_image_id` per sample and `last_trained_at`. Returns updated panel. |
| GET | `/contacts/{contact_id}/recognition-history` | any auth | query `limit` (int, default 50, max 100), `offset` (int, default 0) | `RecognitionHistoryResponse` | `participants` rows where `source='face'` + joined detection rows for this contact, newest first, paginated. |
| POST | `/uploads/photos/batch` | volunteer | multipart `files[]` (max 50 files per request, max 10 MB each), optional query `event_id` (int) | `PhotoIngestBatchResponse` | Create batch row, process all files through existing `process_face_crop` pipeline synchronously, flush per-image progress, return on completion. |
| GET | `/uploads/photos/batch/{batch_id}` | volunteer | — | `PhotoIngestBatchResponse` | Poll batch status and per-image report. 404 if not found. |
| POST | `/pit/{task_id}/enroll` | admin | body `{"contact_id": int}` | `TaskResponse` | Extends existing `pit.enroll_pit_task` (`pit.py:54`): add `contact_id` body param (currently missing — the live endpoint ignores it), call `EnrollmentService.enroll_contact_face` using the detection's crop. |
| POST | `/enrollment/backfill` | admin | `BackfillRequest` (dry_run: bool = True) | `BackfillReportResponse` | Reconcile `compreface_subjects` vs CompreFace live list vs `contacts`. Idempotent. |

The existing `POST /uploads/faces` (`uploads.py:41`) is kept unchanged for single-image live-event uploads.

### 4.2 Services and business rules

#### 4.2.1 `EnrollmentService` — `backend/app/services/enrollment.py` (NEW)

Constructor signature:
```python
class EnrollmentService:
    def __init__(
        self,
        session: AsyncSession,
        compreface: ComprefaceClient,
        storage: FaceStorage,
    ) -> None: ...
```

All methods `async`, all public functions type-hinted (AGENTS.md).

---

**`async def enroll_contact_face(self, contact_id: int, *, image_bytes: bytes | None = None, detection_id: int | None = None, source: str, added_by_id: int | None) -> FaceSample`**

Exactly one of `image_bytes` / `detection_id` must be provided; `ValueError` otherwise (caller converts to 422).

**Input resolution:**
- If `detection_id`: load `Detection`; call `storage.read_detection_full_image(detection.image_path)` (`face_storage.py:55`); 404 if file absent on disk.
- If `image_bytes`: already decoded by caller.

**Subject resolution:**
```
stmt = select(ComprefaceSubject).where(ComprefaceSubject.contact_id == contact_id)
subject = (await session.execute(stmt)).scalar_one_or_none()
```
- If none: create with `subject_name = f"contact:{contact_id}"`, `compreface_subject_id = f"contact_{contact_id}"` (deterministic, URL-safe, consistent with `_find_member_id` convention at `face_pipeline.py:26` where `matched_name = f"member:{id}"`; post-S01 the field is `contact_id` not `member_id`), `enrollment_status = "pending"`, `enrollment_source = source`, `contact_id = contact_id`.
- If existing and `purged_at IS NOT NULL`: raise 409 with detail `"Face data purged; re-enrollment requires consent re-recording (see S08)"`.
- Load `Contact` (post-S01 table `contacts`): 404 if `is_deleted=True` or absent.

**Quality gate:**
- Decode `image_bytes` to BGR numpy via `cv2.imdecode`.
- `quality_ok, reason = FaceQualityGate.check(face_crop)` — checks minimum dimension ≥100px and blur variance ≥100 (`quality_gate.py:11`).
- If fails: raise `ValueError(f"Quality gate failed: {reason}")` → 422 in router; write nothing to DB or disk.

**Atomic enrollment sequence** (ordered to minimize partial-failure surface):
1. `await self.compreface.add_subject(compreface_subject_id)` — idempotent per CompreFace docs; treat 4xx-already-exists as success.
2. `full_path, thumb_path = await self.storage.save_enrollment(compreface_subject_id, face_crop)` — saves under `enrolled/<subject_id>/sample_NNN_{full,thumb}.jpg` (`face_storage.py:63`).
3. `image_id = await self.compreface.add_example(compreface_subject_id, image_bytes)` — see §4.2.3 for `add_example` return value change. If returns falsy: delete the file just written (cleanup), raise 502 "CompreFace add_example failed".
4. In the same DB transaction: flush the new `ComprefaceSubject` if just created, then insert `face_samples` row with `compreface_image_id = image_id`, `image_path = full_path`, `thumb_path = thumb_path`, `source = source`, `detection_id = detection_id`, `added_by_id = added_by_id`.
5. Update `subject.enrollment_status = "active"`, `subject.last_trained_at = utc_now()`, `subject.enrollment_source` (set only if None), recompute `subject.sample_count = await self._count_samples(subject.id)`.
6. `await session.commit()`.
7. Return the `FaceSample` ORM object (caller serializes).

**Rollback rule:** DB and disk writes happen in steps 4-5 together. If commit fails, file written in step 2 is deleted (best-effort; log on delete failure). CompreFace `add_example` writes are not reversible here — S08 owns RTBF purge.

---

**`async def add_sample(self, contact_id: int, image_bytes: bytes, source: str, added_by_id: int | None) -> FaceSample`**

Convenience alias for adding a subsequent sample to an already-enrolled contact. Calls `enroll_contact_face(contact_id, image_bytes=image_bytes, source=source, added_by_id=added_by_id)` — identical logic, existing subject must exist and be active.

---

**`async def remove_sample(self, contact_id: int, sample_id: int) -> None`**

1. Load `FaceSample` where `id = sample_id AND contact_id = contact_id`; 404 if absent.
2. Best-effort CompreFace delete: if `sample.compreface_image_id` is set, call `await self.compreface.delete_example(sample.compreface_image_id)`; log failure, continue (local record is authoritative).
3. Delete the on-disk files (`image_path`, `thumb_path`) best-effort; log failure.
4. `await session.delete(sample); await session.flush()`.
5. Recompute `subject.sample_count`. If `sample_count == 0`: set `subject.enrollment_status = "pending"`.
6. `await session.commit()`.

---

**`async def retrain_subject(self, contact_id: int) -> ComprefaceSubject`**

Re-push all local samples to CompreFace from scratch:
1. Load subject; 404 if absent or `purged_at` set.
2. Load all `face_samples` for this subject ordered by `created_at`.
3. `await self.compreface.add_subject(compreface_subject_id)` — idempotent.
4. For each sample: read bytes from disk (`storage.read_detection_full_image` or direct `Path.read_bytes` on `image_path`); call `add_example`; update `sample.compreface_image_id = new_image_id` in-place. Log per-sample failures but continue.
5. Recompute `subject.sample_count`, set `subject.last_trained_at = utc_now()`, `subject.enrollment_status = "active"`.
6. Commit.

---

**`async def reassign_subject_contact(self, subject_id: int, new_contact_id: int) -> ComprefaceSubject`**

Seam for S11 merge flow. Updates `compreface_subjects.contact_id = new_contact_id` and resets `is_orphan = False`. Does NOT touch CompreFace (the subject_id in CompreFace is unchanged). Existing `face_samples` are reassigned via FK cascade if `contact_id` FK is on the sample; S11 also bulk-updates `face_samples.contact_id` directly.

---

**`async def purge_subject_local(self, contact_id: int) -> None`**

Seam for S08. S07 defines the signature; S08 fills the body with: delete CompreFace subject, delete on-disk files, delete `face_samples`, set `compreface_subjects.purged_at = utc_now()`. In S07 this raises `NotImplementedError("purge logic owned by S08")`.

---

**`async def _count_samples(self, subject_id: int) -> int`**
```python
result = await self.session.execute(
    select(func.count(FaceSample.id)).where(FaceSample.subject_id == subject_id)
)
return result.scalar() or 0
```

#### 4.2.2 `PhotoIngestService` — `backend/app/services/photo_ingest.py` (NEW)

Processes bulk photo batches by delegating each image through the existing `process_face_crop` pipeline.

```python
class PhotoIngestService:
    def __init__(self, ctx: _UploadContext, db_session_factory: Callable) -> None: ...

    async def process_batch(
        self,
        batch_id: int,
        files: list[tuple[str, bytes]],   # (filename, raw_bytes)
        event_id: int | None,
        session: AsyncSession,
    ) -> None: ...
```

**`process_batch` algorithm:**
1. Load `PhotoIngestBatch` by `batch_id`; raise if absent.
2. For each `(filename, raw_bytes)` in `files`:
   a. Decode to BGR numpy: `cv2.imdecode`. On decode failure: increment `batch.errors`, append to `batch.report`, flush and continue.
   b. Enforce ≤25 megapixel limit (same as `uploads.py:79`).
   c. Detect faces: `await ctx.compreface.detect(image_bytes)`. On CompreFace failure: log, increment `batch.errors`, continue.
   d. For each face box: crop, call `process_face_crop(face_crop, camera_id=None, event_id=event_id, ctx=ctx, db_session_factory=db_session_factory)` — existing function unchanged.
   e. Accumulate `faces_detected`, `auto_logged`, `tasks_created`, `skipped`, `deduplicated` from returned `action` dict.
   f. Append row to `batch.report` (cap at 500 rows: once `len(report) >= 500`, accumulate counters but stop appending detail rows).
   g. Increment `batch.processed_images`. Flush `batch` to DB (so polls see live progress).
3. Set `batch.status = "completed"`, `batch.finished_at = utc_now()`. Commit.

**Error handling:** any unhandled exception in outer loop sets `batch.status = "failed"`, `batch.finished_at = utc_now()`, commits, then re-raises. The endpoint catches and returns 500 with the batch_id so the UI can show partial results.

**Performance note:** 50 images × avg 2 faces each = 100 `process_face_crop` calls. Each call makes one CompreFace `recognize` HTTP request (~200ms). Total wall time ≈20s synchronous. This is acceptable for volunteer batch uploads of event photos. For library-scale ingestion (>200 images) S16 can expose the batch as an APScheduler background job; design today for sync, tolerate async later via the same `PhotoIngestBatch` table.

#### 4.2.3 `ComprefaceClient` changes — `backend/app/services/compreface.py`

**`add_example` return type change:**
```python
# Before (compreface.py:157):
async def add_example(self, subject_id: str, image_bytes: bytes) -> bool: ...

# After:
async def add_example(self, subject_id: str, image_bytes: bytes) -> Optional[str]:
    """Returns CompreFace image_id on success, None on failure."""
    url = f"{self.base_url}/api/v1/recognition/faces?subject={subject_id}"
    headers = {"x-api-key": self.recognize_api_key}
    files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
    resp = await self._client.post(url, headers=headers, files=files)
    if resp.status_code not in (200, 201):
        return None
    data = resp.json()
    return data.get("image_id")  # CompreFace returns {"image_id": "uuid", ...}
```

Backward-compatible: all existing callers that only checked truthiness (`if success`) still work because `None` is falsy and a non-empty string is truthy. `queue_manager._process_enrollment` (line 216) currently uses `if success:` — no change needed there until it is refactored to use `EnrollmentService` (also in S07 scope).

**New method `delete_example`:**
```python
async def delete_example(self, image_id: str) -> bool:
    """Delete a specific face example by CompreFace image_id."""
    url = f"{self.base_url}/api/v1/recognition/faces/{image_id}"
    headers = {"x-api-key": self.recognize_api_key}
    resp = await self._client.delete(url, headers=headers)
    return resp.status_code in (200, 204)
```

#### 4.2.4 `task_service.py` auto-enroll wiring — `backend/app/services/task_service.py`

In the `_log_attendance` method and in each of `confirm_task`, `edit_task`, `add_task` (and `admin_override`), after the task resolves (status → `"resolved"`), check if the detection's `compreface_subject_id` has no active `ComprefaceSubject` row for the resolved `contact_id`, and if so, trigger enrollment.

Add a new private helper:
```python
async def _maybe_auto_enroll(self, detection: Detection, contact_id: int) -> None:
    """If this detection's face has not been enrolled, enroll it now."""
    if not detection or not detection.image_path:
        return
    # Check if subject already enrolled for this contact
    stmt = select(ComprefaceSubject).where(
        ComprefaceSubject.contact_id == contact_id,
        ComprefaceSubject.enrollment_status == "active",
    )
    existing = (await self.session.execute(stmt)).scalar_one_or_none()
    if existing and existing.sample_count > 0:
        return  # already enrolled; skip (don't add duplicate sample from every detection)
    # Enroll using the detection crop
    from app.services.enrollment import EnrollmentService
    from app.services.compreface import ComprefaceClient
    from app.services.face_storage import FaceStorage
    from app.config import legacy_settings
    cf = ComprefaceClient()
    try:
        svc = EnrollmentService(self.session, cf, FaceStorage(legacy_settings.STORAGE_PATH))
        await svc.enroll_contact_face(
            contact_id,
            detection_id=detection.id,
            source="detection",
            added_by_id=None,
        )
    except Exception:
        logger.exception("Auto-enroll failed for contact=%s detection=%s", contact_id, detection.id)
        # Non-fatal: attendance is logged regardless
    finally:
        await cf.close()
```

Call `await self._maybe_auto_enroll(detection, member_id)` at the end of `_log_attendance` after the `Participant` row is committed (post-S01: `Participant`, not `Attendance`). This is called for `confirm_task`, `edit_task`, `add_task`, and `admin_override` paths.

**`pit.enroll_pit_task` (`routers/pit.py:54`):** Replace the stub that only stamps `matched_name`/`is_enrolled` with a call to `EnrollmentService.enroll_contact_face`. The endpoint already receives `contact_id` as a query param; change the param to a required JSON body `{"contact_id": int}` (to match the endpoint table in §4.1) and validate it:

```python
@router.post("/{task_id}/enroll")
async def enroll_pit_task(
    task_id: int,
    body: PitEnrollRequest,   # new schema: contact_id: int
    db: AsyncSession = Depends(get_db),
    user = Depends(require_admin),
):
    task = await db.get(Task, task_id)
    ...  # existing 404 guard
    detection = await db.get(Detection, task.detection_id)
    # Call EnrollmentService
    from app.services.enrollment import EnrollmentService
    ...
    await svc.enroll_contact_face(
        body.contact_id,
        detection_id=detection.id,
        source="detection",
        added_by_id=user["sub"],
    )
    # Then update task/detection/pit as before
    task.pit_status = "enrolled"
    task.status = "resolved"
    detection.matched_name = f"member:{body.contact_id}"
    detection.is_enrolled = True
    ...
    await db.commit()
```

#### 4.2.5 Backfill job — `POST /enrollment/backfill`

**`BackfillService.run(session, compreface, storage, dry_run=True) -> BackfillReport`:**
1. Fetch live CompreFace subject list: `await compreface.list_subjects()` → `{compreface_subject_id: str}` set.
2. Load all `compreface_subjects` rows from DB.
3. For each DB subject:
   - If `compreface_subject_id` not in live set: mark `is_orphan = True` (CompreFace subject deleted externally).
   - If `contact_id` is NULL or contact row is absent/deleted: mark `is_orphan = True`.
   - If `purged_at IS NOT NULL`: skip (already purged by S08).
   - If `face_samples` count for this subject is 0 and the `enrolled/<subject_id>/` directory on disk has images: seed `face_samples` rows from on-disk files (`source = "backfill"`). `compreface_image_id` will be NULL (cannot recover from CompreFace — best-effort).
4. For each CompreFace subject with no matching DB row: log as "unregistered CompreFace subject" in report (do not create DB rows — could be from external tools; admin reviews).
5. If `dry_run=False`: commit all changes. If `dry_run=True`: return report without committing.
6. Return `BackfillReport(orphans_found, samples_seeded, unregistered_cf_subjects, dry_run)`.

**Idempotency:** `is_orphan = True` is idempotent; `face_samples` upsert checks `image_path` uniqueness to avoid duplicate rows on re-run.

#### 4.2.6 `queue_manager._process_enrollment` refactor — `backend/app/services/queue_manager.py:163`

Replace the inline enrollment logic with a delegation:
```python
async def _process_enrollment(self) -> bool:
    async with self.db_session_factory() as session:
        result = await session.execute(
            select(ComprefaceSubject)
            .where(ComprefaceSubject.enrollment_status == "pending")
            .where(ComprefaceSubject.purged_at.is_(None))
            .limit(5)
        )
        subjects = result.scalars().all()
        if not subjects: return False
        from app.services.enrollment import EnrollmentService
        client = ComprefaceClient()
        storage = FaceStorage(legacy_settings.STORAGE_PATH)
        svc = EnrollmentService(session, client, storage)
        try:
            for subject in subjects:
                # Find the most recent enrolled detection for this subject
                det = (await session.execute(
                    select(Detection)
                    .where(Detection.compreface_subject_id == subject.compreface_subject_id)
                    .where(Detection.is_enrolled == True)
                    .order_by(Detection.created_at.desc())
                    .limit(1)
                )).scalar_one_or_none()
                if not det: continue
                try:
                    await svc.enroll_contact_face(
                        subject.contact_id,
                        detection_id=det.id,
                        source="detection",
                        added_by_id=None,
                    )
                except Exception:
                    logger.exception("Worker enrollment failed: subject=%s", subject.id)
        finally:
            await client.close()
        return True
```

Remove the now-duplicated inline logic (the 30 lines currently at `queue_manager.py:181-234`).

### 4.3 New router `backend/app/routers/enrollment.py`

```python
router = APIRouter(prefix="/contacts", tags=["enrollment"])
```

Registered in `main.py` after `pit.router` with `dependencies=[Depends(check_setup_complete)]`.

All endpoint handlers:
- Construct `_UploadContext()` (reused from `uploads.py`) for `ComprefaceClient` + `FaceStorage`.
- Construct `EnrollmentService(db, compreface, storage)`.
- Return Pydantic response models.
- Close `ComprefaceClient` in `finally` blocks.

Error-to-HTTP mapping:
- `ValueError` from quality gate or missing param → 422.
- `FileNotFoundError` from `read_detection_full_image` → 404 "Crop image not found on disk".
- `502` from `compreface.add_example` returning None → 502 "CompreFace enrollment failed".
- `409` from `purged_at` check → 409 "Subject purged".

### 4.4 New Pydantic schemas — `backend/app/schemas.py`

Add to the end of `schemas.py`:

```python
class FaceSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contact_id: int
    subject_id: int
    image_path: str          # /storage/-prefixed URL for frontend
    thumb_path: Optional[str]
    source: str
    quality: Optional[float]
    detection_id: Optional[int]
    compreface_image_id: Optional[str]
    added_by_id: Optional[int]
    created_at: datetime

class FacePanelResponse(BaseModel):
    subject_id: Optional[int]
    compreface_subject_id: Optional[str]
    enrollment_status: Optional[str]       # pending | active | None (not enrolled)
    sample_count: int
    last_trained_at: Optional[datetime]
    is_orphan: bool
    purged_at: Optional[datetime]          # null until S08
    samples: List[FaceSampleResponse]

class RecognitionHistoryItem(BaseModel):
    participant_id: int
    event_id: int
    event_title: str
    occurrence_date: Optional[datetime]
    source: str                            # "face"
    detection_id: Optional[int]
    confidence: Optional[float]
    tier: Optional[str]
    created_at: datetime

class RecognitionHistoryResponse(BaseModel):
    contact_id: int
    total: int
    items: List[RecognitionHistoryItem]

class PhotoIngestBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    total_images: int
    processed_images: int
    faces_detected: int
    auto_logged: int
    tasks_created: int
    skipped: int
    deduplicated: int
    errors: int
    report: List[dict]
    created_at: datetime
    finished_at: Optional[datetime]

class PitEnrollRequest(BaseModel):
    contact_id: int

class BackfillRequest(BaseModel):
    dry_run: bool = True

class BackfillReportResponse(BaseModel):
    dry_run: bool
    orphans_found: int
    samples_seeded: int
    unregistered_cf_subjects: int
    message: str
```

### 4.5 ORM model additions — `backend/app/models.py`

Add after `PitQueue` (line 261):

```python
class FaceSample(Base):
    __tablename__ = "face_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("compreface_subjects.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    quality: Mapped[Optional[float]] = mapped_column(Numeric(5, 3), nullable=True)
    detection_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("detections.id", ondelete="SET NULL"), nullable=True
    )
    compreface_image_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    added_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PhotoIngestBatch(Base):
    __tablename__ = "photo_ingest_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="processing")
    total_images: Mapped[int] = mapped_column(Integer, default=0)
    processed_images: Mapped[int] = mapped_column(Integer, default=0)
    faces_detected: Mapped[int] = mapped_column(Integer, default=0)
    auto_logged: Mapped[int] = mapped_column(Integer, default=0)
    tasks_created: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    deduplicated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```

Extend `ComprefaceSubject` (starting at `models.py:87`) with the four new columns:
```python
enrollment_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
last_trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
is_orphan: Mapped[bool] = mapped_column(Boolean, default=False)
purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```

Update `ComprefaceSubject` FK: post-S01 the FK target is `contacts.id`, not `civicrm_members.contact_id` (was `models.py:94`). This FK change is the S01 migration's job; this spec assumes it is already landed.

### 4.6 File-by-file summary

| Action | File | Change |
|---|---|---|
| CREATE | `backend/app/services/enrollment.py` | `EnrollmentService` with all 6 methods |
| CREATE | `backend/app/services/photo_ingest.py` | `PhotoIngestService.process_batch` |
| CREATE | `backend/app/routers/enrollment.py` | 5 endpoints for `/contacts/{id}/faces*` + `/enrollment/backfill` |
| MODIFY | `backend/app/routers/uploads.py` | Add `POST /uploads/photos/batch`, `GET /uploads/photos/batch/{batch_id}` |
| MODIFY | `backend/app/routers/pit.py` | `enroll_pit_task`: add `PitEnrollRequest` body, call `EnrollmentService` |
| MODIFY | `backend/app/services/compreface.py` | `add_example` returns `Optional[str]`; add `delete_example` |
| MODIFY | `backend/app/services/task_service.py` | Add `_maybe_auto_enroll`; call it in `_log_attendance` |
| MODIFY | `backend/app/services/queue_manager.py` | Refactor `_process_enrollment` to delegate to `EnrollmentService` |
| MODIFY | `backend/app/models.py` | Add `FaceSample`, `PhotoIngestBatch`; extend `ComprefaceSubject` |
| MODIFY | `backend/app/schemas.py` | Add 8 new schemas listed in §4.4 |
| MODIFY | `backend/app/main.py` | `app.include_router(enrollment.router, dependencies=[Depends(check_setup_complete)])` |
| CREATE | `backend/alembic/versions/<rev>_s07_face_enrollment_and_samples.py` | Migration per §3.4 |
| CREATE | `backend/tests/test_enrollment.py` | Per §8 |
| CREATE | `backend/tests/test_photo_ingest.py` | Per §8 |

---

## 5. Frontend

### 5.1 Pages and routes

#### NEW: `BulkPhotoUploadPage` — `frontend/src/pages/BulkPhotoUploadPage.tsx`

Route: `/bulk-upload` — `AdminRoute` (admin only for now; can open to volunteer in a follow-up).

**UX flow (mobile-first):**
1. Header with back arrow and title "Bulk Photo Upload".
2. Optional event selector dropdown (fetch events from `/events`; selected event_id is passed to the batch endpoint). Clear selection shows "No event (enrollment only)".
3. File drop zone + tap-to-browse (accept `image/*`, max 50 files). On mobile: `<input type="file" multiple accept="image/*" capture="environment">` for camera roll access.
4. File list preview (filename + size + thumbnail 40×40 via `URL.createObjectURL`). Remove-individual button.
5. "Upload & Process" button. On submit: chunk files into groups of 50 (front-end enforced); for each chunk call `POST /uploads/photos/batch` with `multipart/form-data files[]` + query `event_id`. On 200 receive `batch_id`.
6. Progress section: for each batch_id poll `GET /uploads/photos/batch/{batch_id}` every 2s until `status !== 'processing'`. Show `processed_images / total_images` progress bar; `faces_detected`, `tasks_created`, `auto_logged`, `errors` counters live.
7. On completion: show per-image report table (filename, faces_detected, outcome). Errors shown with red badge. Link "View Task Queue" if `tasks_created > 0`.
8. Empty state: "Select photos from a past event to start enrolling faces." Loading state: `LoadingState` component from `frontend/src/components/ui/StateViews.tsx`. Error state: `ErrorState` with retry.

**TanStack Query usage:**
- `useQuery({ queryKey: ['batch', batchId], queryFn: () => api.get(...), refetchInterval: batch.status === 'processing' ? 2000 : false, enabled: !!batchId })` — auto-stops polling when done.
- No stale-while-revalidate for live progress (refetchInterval provides freshness).

**Role gating:** `AdminRoute` wraps the page. Extends to volunteer in a later pass via `ProtectedRoute` once tested.

#### MODIFIED: Contact detail page face panel — S03 provides the `/contacts/:id` detail page; S07 adds the `FacePanel` component to it.

Add a "Face Recognition" section to `frontend/src/pages/ContactDetailPage.tsx` (created by S03) that renders `<FacePanel contactId={id} />`.

#### MODIFIED: `PitPage` — `frontend/src/pages/PitPage.tsx`

The existing "Enroll" button at `PitPage.tsx:89` calls `api.post('/pit/${taskId}/enroll')` with no body. Change to:
1. On "Enroll" click: open `MemberSearchModal` (already exists at `frontend/src/components/tasks/MemberSearchModal.tsx`) in `mode='add'` to pick the contact.
2. On contact selected: call `api.post('/pit/${taskId}/enroll', { contact_id: member.id })`.
3. Invalidate `['pit']` query. Toast on success/failure.

### 5.2 New component: `FacePanel`

**File:** `frontend/src/components/contacts/FacePanel.tsx`

**Props:**
```typescript
interface FacePanelProps {
  contactId: number;
  readOnly?: boolean;  // viewer role: no add/remove/retrain
}
```

**Query:**
```typescript
const { data: panel, isLoading, isError } = useQuery({
  queryKey: ['contact-faces', contactId],
  queryFn: () => api.get<FacePanelData>(`/contacts/${contactId}/faces`).then(r => r.data),
  staleTime: 30_000,
});
```

**Rendered sections:**

1. **Status bar** — enrollment status badge (pill: `active` green / `pending` amber / `not enrolled` gray). `sample_count` counter. `is_orphan` warning banner. `purged_at` warning banner (S08 placeholder: shown as "Biometric data purged" if non-null).

2. **Sample grid** — 2-column grid of enrolled photo thumbnails (served via `/api/storage/{thumb_path}`). Each thumbnail card:
   - 80×80 thumbnail with rounded-xl border.
   - Source badge (`detection` / `manual` / `bulk_ingest` / `backfill`) in foreground/50 text.
   - Remove button (trash icon, min-h-[44px] touch target) — hidden if `readOnly`. On click: `ConfirmDialog` ("Remove this face sample?"); on confirm: `DELETE /contacts/{id}/faces/{sample_id}`, invalidate `['contact-faces', contactId]`.

3. **Add sample** (hidden if `readOnly` or `purged_at` set) — "Add Photo" button opens an `<input type="file" accept="image/*">`. On file select: `POST /contacts/{id}/faces` with multipart `file`. 422 → toast with quality reason. 502 → toast "CompreFace error".

4. **Re-train** (shown only if `sample_count > 0` and not `readOnly`) — "Re-train" button: `POST /contacts/{id}/faces/retrain`. Loading spinner on button. Invalidate panel query on success.

5. **Recognition history link** — "View recognition history" text link navigating to a modal or sub-route showing `GET /contacts/{id}/recognition-history` results (simplest: inline accordion table, max 20 rows with "Show more" if `total > 20`).

**Empty state:** if `panel.samples.length === 0` and `panel.enrollment_status === null`: `<EmptyState icon={Camera} message="No face enrolled" sub="Add a photo or process an event batch" />`.

**Error state:** `<ErrorState message="Failed to load face data" onRetry={() => queryClient.invalidateQueries(...)} />`.

**Mobile-first:** full width on mobile (375px), side-by-side 2-column grid of photos. On desktop (≥1024px, inside contact detail card): same layout scaled.

### 5.3 New route in `App.tsx`

Add to `frontend/src/App.tsx`:
```tsx
import { BulkPhotoUploadPage } from '@/pages/BulkPhotoUploadPage';
// ...
<Route path="/bulk-upload" element={<AdminRoute><BulkPhotoUploadPage /></AdminRoute>} />
```

Add "Bulk Upload" link in `BottomNav.tsx` admin "More" sheet (alongside existing `/pit`, `/logs`, `/dashboard` links).

### 5.4 New TypeScript types — `frontend/src/types/index.ts` (or new `faces.ts`)

```typescript
export interface FaceSample {
  id: number;
  contact_id: number;
  subject_id: number;
  image_path: string;
  thumb_path: string | null;
  source: 'manual' | 'detection' | 'bulk_ingest' | 'backfill';
  quality: number | null;
  detection_id: number | null;
  compreface_image_id: string | null;
  added_by_id: number | null;
  created_at: string;
}

export interface FacePanelData {
  subject_id: number | null;
  compreface_subject_id: string | null;
  enrollment_status: 'pending' | 'active' | null;
  sample_count: number;
  last_trained_at: string | null;
  is_orphan: boolean;
  purged_at: string | null;
  samples: FaceSample[];
}

export interface PhotoIngestBatch {
  id: number;
  status: 'processing' | 'completed' | 'failed';
  total_images: number;
  processed_images: number;
  faces_detected: number;
  auto_logged: number;
  tasks_created: number;
  skipped: number;
  deduplicated: number;
  errors: number;
  report: Array<{
    filename: string;
    faces_detected: number;
    outcome: string;
    error?: string;
  }>;
  created_at: string;
  finished_at: string | null;
}
```

### 5.5 Design tokens and UX rules

- No hardcoded hex colors; status badges use: `bg-green-100 text-green-800` for `active`, `bg-amber-100 text-amber-800` for `pending`, `bg-background text-foreground/50` for not-enrolled. (These map to Tailwind utility classes whose values come from the CSS vars set in `index.css` for dark mode compatibility.)
- Every destructive action (remove sample) goes through `ConfirmDialog` (`components/ui/ConfirmDialog.tsx`) with `destructive` variant (AGENTS.md convention).
- File inputs with `accept="image/*"` and minimum touch target `min-h-[44px]` on all action buttons.
- `sonner` toast for every mutation success/failure (`toast.success` / `toast.error(err.response?.data?.detail || 'fallback')`).
- `LoadingState` / `EmptyState` / `ErrorState` from `components/ui/StateViews.tsx` — do not inline custom loading/empty divs.

### 5.6 File-by-file frontend summary

| Action | File | Change |
|---|---|---|
| CREATE | `frontend/src/pages/BulkPhotoUploadPage.tsx` | Full bulk-upload UI per §5.1 |
| CREATE | `frontend/src/components/contacts/FacePanel.tsx` | Face panel component per §5.2 |
| MODIFY | `frontend/src/pages/PitPage.tsx` | Wire "Enroll" to `MemberSearchModal` → `POST /pit/{id}/enroll` with body |
| MODIFY | `frontend/src/pages/ContactDetailPage.tsx` (S03) | Add `<FacePanel contactId={id} />` section |
| MODIFY | `frontend/src/App.tsx` | Add `/bulk-upload` route |
| MODIFY | `frontend/src/components/layout/BottomNav.tsx` | Add "Bulk Upload" link in admin More sheet |
| MODIFY | `frontend/src/types/index.ts` | Add `FaceSample`, `FacePanelData`, `PhotoIngestBatch` types |

---

## 6. Migration / data

This sprint introduces no data migration step (migrations bring the schema; the backfill job brings the data). The operational workflow post-deploy:

1. Admin runs `POST /enrollment/backfill` with `dry_run=true` — reviews report of orphan subjects and seedable samples. Confirms counts look correct.
2. Admin runs `POST /enrollment/backfill` with `dry_run=false` — seeds `face_samples` rows from on-disk `enrolled/` directories. Does not touch CompreFace.
3. (Optional) Admin runs `POST /contacts/{id}/faces/retrain` for specific contacts to push freshly-seeded samples to CompreFace under the correct `compreface_subject_id`.
4. For historical-photo bootstrap: admin navigates to `/bulk-upload`, selects an optional event, uploads a batch of historical photos. The pipeline detects + recognizes + routes to volunteer queue. Volunteers confirm identities. Auto-enroll fires on confirmation. CompreFace improves recognition on the next live event.

**Critical S01 dependency:** `compreface_subjects.contact_id` FK must already point to `contacts.id` (post-S01 schema). If S01 is not yet landed, the migration will fail because the FK target table `contacts` does not exist. The S07 migration must list S01's final migration as `down_revision`.

---

## 7. Acceptance criteria

1. `POST /contacts/42/faces` with a valid JPEG `file` (a clear face image ≥100px) for a contact that has no prior enrollment: returns 201, creates a `ComprefaceSubject` row with `contact_id=42`, creates a `face_samples` row with `compreface_image_id` populated, and the subject appears in `GET /contacts/42/faces` with `enrollment_status="active"` and `sample_count=1`.
2. `POST /contacts/42/faces` with a blurry image that fails `FaceQualityGate.check` returns 422 with a reason string. No `FaceSample` row or CompreFace call is made.
3. `DELETE /contacts/42/faces/{sample_id}` removes the sample row, calls `ComprefaceClient.delete_example`, and `GET /contacts/42/faces` returns `sample_count=0` with `enrollment_status="pending"`.
4. `POST /contacts/42/faces/retrain` re-pushes all face_samples images to CompreFace and updates `last_trained_at` to within 5 seconds of the call.
5. `GET /contacts/42/recognition-history?limit=10` returns only `participants` rows with `source="face"` (or linked detections) for contact 42, newest first, with `total` ≥ actual row count.
6. `POST /uploads/photos/batch` with 3 JPEG files (at least one containing a detectable face) returns `PhotoIngestBatchResponse` with `status="completed"`, `total_images=3`, `processed_images=3`, and `faces_detected ≥ 1`.
7. `GET /uploads/photos/batch/{batch_id}` returns the same batch with correct counters and a `report` array of length = `total_images`.
8. A volunteer (non-admin) can call `POST /uploads/photos/batch` and `POST /contacts/{id}/faces` but gets 403 from `POST /enrollment/backfill`.
9. A viewer (post-S15 role) can call `GET /contacts/{id}/faces` and `GET /contacts/{id}/recognition-history` but gets 403 from `POST /contacts/{id}/faces` (enrollment mutations require volunteer or higher).
10. After a task is resolved via `confirm_task` with `required_approvals=1` and the detection had no prior enrolled subject for that contact, `compreface_subjects` gains a new row with `enrollment_status="active"` for that contact and `face_samples` has one row linked to it.
11. `PUT /pit/{task_id}/enroll` with `{"contact_id": 99}` for a pit task resolves the task, sets `pit_status="enrolled"`, creates or updates the `ComprefaceSubject`, creates a `FaceSample`, and calls CompreFace `add_subject`/`add_example` (verifiable via mocking in tests).
12. `POST /enrollment/backfill?dry_run=true` returns a `BackfillReportResponse` without modifying any DB rows (verified by checking `face_samples` count before and after).
13. `POST /enrollment/backfill?dry_run=false` on a subject with `enrollment_source=NULL` and images in `enrolled/<subject_id>/` on disk creates `face_samples` rows (`source="backfill"`) and does not raise.
14. `POST /contacts/42/faces` when `compreface_subjects.purged_at IS NOT NULL` returns 409 with the re-consent message.
15. `add_example` returning `None` (mocked CompreFace failure) causes `POST /contacts/{id}/faces` to return 502 and to leave no `face_samples` row in the DB (atomicity).
16. `FacePanel` on the contact detail page shows the enrolled photo thumbnails. Clicking "Remove" opens `ConfirmDialog`; confirming calls `DELETE /contacts/{id}/faces/{sample_id}` and removes the thumbnail from the grid. The UI uses only Tailwind design tokens, not hardcoded hex.
17. `BulkPhotoUploadPage`: selecting 3 images and clicking "Upload & Process" displays a progress bar that updates, then shows a per-image report on completion. `npm run build` and `npm run lint` pass with zero errors.

---

## 8. Test plan

### Backend — `backend/tests/test_enrollment.py`

```python
# pytest + pytest-asyncio; CompreFace mocked via unittest.mock.AsyncMock

@pytest.mark.asyncio
async def test_enroll_contact_face_from_image_bytes_creates_subject_and_sample(db_session, admin_user, sample_contact):
    """POST /contacts/{id}/faces with valid image creates subject + sample."""

@pytest.mark.asyncio
async def test_enroll_quality_gate_failure_raises_422(db_session, sample_contact):
    """Quality gate failure: no DB rows written."""

@pytest.mark.asyncio
async def test_enroll_compreface_failure_rolls_back_no_sample_row(db_session, sample_contact, monkeypatch):
    """If add_example returns None, no face_samples row is persisted."""
    # monkeypatch ComprefaceClient.add_example to return None

@pytest.mark.asyncio
async def test_enroll_purged_subject_returns_409(db_session, sample_contact):
    """Enrolling a purged contact raises 409."""

@pytest.mark.asyncio
async def test_remove_sample_decrements_count_and_calls_delete_example(db_session, sample_contact, monkeypatch):
    """remove_sample: calls delete_example, deletes row, recomputes sample_count."""

@pytest.mark.asyncio
async def test_retrain_subject_updates_last_trained_at(db_session, sample_contact, monkeypatch):
    """retrain_subject: calls add_subject + add_example for each sample; last_trained_at updated."""

@pytest.mark.asyncio
async def test_enroll_endpoint_volunteer_can_enroll(client, volunteer_auth_headers, sample_contact, monkeypatch):
    """POST /contacts/{id}/faces — volunteer role allowed."""

@pytest.mark.asyncio
async def test_enroll_endpoint_viewer_cannot_enroll(client, viewer_auth_headers, sample_contact):
    """POST /contacts/{id}/faces — viewer gets 403."""

@pytest.mark.asyncio
async def test_backfill_dry_run_no_db_changes(client, admin_auth_headers, db_session, monkeypatch):
    """POST /enrollment/backfill with dry_run=true: row counts unchanged."""

@pytest.mark.asyncio
async def test_pit_enroll_creates_subject_and_resolves_task(client, admin_auth_headers, db_session, sample_task, sample_contact, monkeypatch):
    """POST /pit/{task_id}/enroll with contact_id: task resolved, subject created."""

@pytest.mark.asyncio
async def test_pit_enroll_missing_contact_id_returns_422(client, admin_auth_headers, sample_task):
    """POST /pit/{task_id}/enroll with no body returns 422."""

@pytest.mark.asyncio
async def test_auto_enroll_on_task_resolve(db_session, sample_contact, sample_detection, monkeypatch):
    """When task resolves via confirm_task, _maybe_auto_enroll fires and subject becomes active."""
```

### Backend — `backend/tests/test_photo_ingest.py`

```python
@pytest.mark.asyncio
async def test_batch_with_three_images_returns_completed(client, volunteer_auth_headers, monkeypatch):
    """POST /uploads/photos/batch with 3 valid images: status=completed, processed_images=3."""

@pytest.mark.asyncio
async def test_batch_face_detected_creates_task(client, volunteer_auth_headers, db_session, monkeypatch):
    """A face detected in a batch image creates a Task row."""

@pytest.mark.asyncio
async def test_batch_image_decode_failure_increments_errors(client, volunteer_auth_headers):
    """A non-image file in the batch increments errors, does not abort batch."""

@pytest.mark.asyncio
async def test_batch_poll_returns_live_status(client, volunteer_auth_headers, monkeypatch):
    """GET /uploads/photos/batch/{id} returns current status and counters."""

@pytest.mark.asyncio
async def test_batch_report_capped_at_500_entries(client, volunteer_auth_headers, monkeypatch):
    """Batch with >500 images does not grow report beyond 500 entries."""

@pytest.mark.asyncio
async def test_batch_volunteer_can_upload(client, volunteer_auth_headers, monkeypatch):
    """Volunteer role can POST /uploads/photos/batch."""

@pytest.mark.asyncio
async def test_batch_viewer_cannot_upload(client, viewer_auth_headers):
    """Viewer role gets 403 on POST /uploads/photos/batch."""
```

### Frontend — vitest

**`frontend/src/components/contacts/FacePanel.test.tsx`:**
- Renders enrolled samples when API returns data with `enrollment_status: "active"`.
- Shows `EmptyState` when `samples = []` and `enrollment_status = null`.
- "Remove" button opens `ConfirmDialog`; confirming triggers `DELETE` call and invalidates query.
- "Re-train" button triggers `POST /contacts/{id}/faces/retrain` and shows loading state.
- If `readOnly=true`, no add/remove/retrain buttons are rendered.
- `purged_at` non-null shows warning banner; add buttons hidden.

**`frontend/src/pages/BulkPhotoUploadPage.test.tsx`:**
- File drop zone renders with accessible label.
- Selecting 3 files shows them in the preview list with file names.
- Submitting calls `POST /uploads/photos/batch` with correct `FormData` contents.
- Progress bar updates when poll returns `processed_images > 0`.
- Completion shows per-image report table.

### Build and lint gate

```bash
# From frontend/
npm run build   # must exit 0
npm run lint    # must exit 0

# From backend/
DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q
```

---

## 9. Rollout / rollback / risks

### Rollout order

1. Apply Alembic migration (`alembic upgrade head`) — additive only; no existing rows modified.
2. Deploy backend with new `enrollment.py` router and modified files.
3. Verify `GET /contacts/{any_id}/faces` returns `{"enrollment_status": null, "samples": [], ...}` for contacts that have no subject.
4. Run `POST /enrollment/backfill` with `dry_run=true` — review report in logs; confirm no unexpected orphans.
5. Run `POST /enrollment/backfill` with `dry_run=false` — seeds `face_samples` from on-disk crops.
6. Deploy frontend. Test `FacePanel` on a known-enrolled contact.
7. Manually test one `BulkPhotoUploadPage` batch with a handful of photos.
8. Monitor volunteer task queue counts post-deploy to confirm auto-enroll does not flood the queue (it should not — it only enrolls on task resolution, which is an existing flow).

### Rollback

If a critical defect is found post-deploy:
- Run `alembic downgrade -1` to drop `face_samples`, `photo_ingest_batches`, and the four new columns on `compreface_subjects`. **This is data-lossy** for any `face_samples` rows written after deploy. The `compreface_subjects` rows themselves and CompreFace external data are unchanged.
- Redeploy prior frontend/backend images.
- The `face_samples` rows written to CompreFace during the window between deploy and rollback are permanent until a future RTBF purge.

### Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `add_example` return-type change (`bool` → `Optional[str]`) breaks existing callers | Low (only 2 callers: `queue_manager.py` uses `if success` which is still truthy-safe; `_process_enrollment` refactored in this same sprint) | Medium | Audit all `add_example` call sites before merge; add regression test |
| CompreFace returns `image_id = None` even on success (API version mismatch) | Low-Medium (depends on CompreFace version) | Medium (silences per-sample targeted delete) | Fall back: if `image_id` is None log a warning; `remove_sample` falls back to full subject delete + retrain |
| Bulk-ingest timing out on large batches (>50 images synchronous) | Medium (50 images × 200ms = 10s) | Medium (502 timeout at nginx 60s proxy_read_timeout) | Client chunks uploads to ≤50 files per request; nginx `proxy_read_timeout` increased to 120s if needed |
| Auto-enroll in `_maybe_auto_enroll` fires on every task resolution, saturating CompreFace | Low (guarded by `existing.sample_count > 0` skip) | Medium | Log every auto-enroll call; if frequency becomes problematic add rate-cap to the method |
| `backfill` reading wrong `enrolled/` path when `STORAGE_PATH` differs across environments | Low | Low | Backfill uses `FaceStorage(legacy_settings.STORAGE_PATH)` — same config path used everywhere else |
| S01 not yet landed when S07 migration runs | Medium (sprint ordering) | High (FK target `contacts.id` absent → migration fails) | Explicitly block S07 migration on S01 head; note in §6 |
| Biometric data written to CompreFace before consent recorded (S08 not yet landed) | Certain (S07 precedes S08) | Medium (data governance) | S08 adds consent; S07 stores `purged_at` column as the gate; admin is advised to complete S08 before enrolling real members at scale |

---

## 10. Open questions & pending owner artifacts

| # | Question | Owner | Blocks |
|---|---|---|---|
| 1 | What `compreface_subject_id` format was used for historically seeded subjects (external script)? The backfill job needs to match existing CompreFace subjects to contacts. | Owner + DBA | Backfill (§4.2.5) |
| 2 | Are there existing `enrolled/<subject_id>/` directories on disk with crops from the external seeding script? If yes, what is the `subject_id` naming convention used? | Owner | Backfill seed logic |
| 3 | Target CompreFace version — confirm that `POST /api/v1/recognition/faces` returns a body with `"image_id"` field. The `add_example` return-type change depends on this. | Owner / infra | `ComprefaceClient.add_example` change |
| 4 | `DELETE /api/v1/recognition/faces/{image_id}` — confirm it exists and is accessible with the `recognize_api_key`. Per CompreFace docs this requires the Recognition API key, not the Detection API key. | Owner / infra | `remove_sample` targeted delete |
| 5 | Should the bulk upload page be accessible to volunteers or admin only? Decision: admin only for the initial release (§5.1). Confirm with owner. | Owner | `BulkPhotoUploadPage` route guard |
| 6 | What is the largest single batch expected at launch (back-catalog of event photos)? If >500 images, async background job approach must be designed now rather than retrofit. | Owner | `PhotoIngestService` sync vs. async |
| 7 | Right-to-be-forgotten timeline: can S08 land immediately after S07 in the same deploy window, or will there be enrolled contacts before consent is recorded? | Owner | Biometric governance sequencing |

---

*Cross-sprint dependencies:*
- **S01** (Schema Inversion): `contacts` table and `compreface_subjects.contact_id → contacts.id` FK must exist before S07 migration runs. If S01 is in-flight, S07 migration must use S01's head as `down_revision`.
- **S03** (Contact CRUD): `ContactDetailPage` must exist for `FacePanel` to be mounted in.
- **S04** (Event CRUD): `events.id` FK used in `photo_ingest_batches` must exist.
- **S08** (Biometric Consent): S07 defines `purged_at` column and gates enrollment on it; S08 owns the destructive purge and `biometric_consent` table. These two sprints must deploy in sequence.
- **S11** (Find & Merge): calls `EnrollmentService.reassign_subject_contact`; that seam is defined here.
- **S15** (RBAC 3 Roles): `viewer` role needs `require_volunteer` to gate all face-write endpoints correctly; until S15 lands, the `viewer` role does not exist and two-role check (`admin`/`volunteer`) in `require_volunteer` is sufficient.
- **S22** (Name Matching): community report photos are explicitly out of scope for S07. Do not merge or confuse the two ingestion paths.
