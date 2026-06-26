# Backend Services

A modular overview of Project Seraphim's FastAPI service layer. Each service owns a specific domain: face/vision, CRM/contact sync, data migration, matching/dedup, queue management, and auditing.

---

## Face & Vision Services

### face_pipeline.py
Core asynchronous pipeline processing a single face crop through quality gate → CompreFace recognition → dedup → DB insertion → SSE broadcast.

**Key flow:**
1. Quality gate check (min face size, blur score)
2. Recognition via CompreFace (returns subject_id, similarity %, tier)
3. Dedup cache check (avoid duplicate detections within 60-second window)
4. Save detection snapshot (thumbnail + full) to disk
5. Insert Detection row + route based on tier:
   - **Tier 100** (≥98% similarity) → auto-log Participant attendance + SSE update
   - **Tier 91-99** (≥91%) → create Task (requires 1 approval)
   - **Below 90** → create Task (requires 2 approvals)
6. SSE broadcast for real-time volunteer queue updates

**Scoring context:** Volunteer-earned points: confirm=1, edit=2, add=1 (applied via task_service._update_volunteer_stats)

---

### compreface.py
HTTP client wrapping the [CompreFace](https://github.com/exadel-inc/CompreFace) API with retry logic (exponential backoff, 3 attempts).

**Methods:**
- `detect(image_bytes)` → list of face bounding boxes
- `recognize(image_bytes)` → best match subject_id + similarity score + tier
- `add_subject(subject_id)` → create/register subject (idempotent)
- `add_example(subject_id, image_bytes)` → upload enrollment sample (returns image UUID)
- `delete_example(image_id)` → remove enrollment sample
- `list_subjects()` → all subjects in CompreFace
- `delete_subject(subject_id)` → purge subject

**Tier mapping:** Uses admin-configurable thresholds; default: 100 (≥0.98), 91-99 (≥0.91), below90, unknown.

**API keys:** Separate keys for Detection and Recognition services; falls back to legacy single key for backward compatibility.

---

### face_storage.py
Local filesystem driver for saving and reading face detection/enrollment snapshots.

**Methods:**
- `save_detection(frame, face_box)` → (full_path, thumb_path) — 300×300 thumbnail + full JPEG
- `read_detection_full_image(thumb_path)` → bytes — reconstruct full image from thumb path
- `save_enrollment(subject_id, face_crop)` → (full_path, thumb_path) — numbered samples for subject

**Structure:** `faces/YYYY/MM/DD/det_<timestamp>_<uuid>_[full|thumb].jpg` for detections; `enrolled/<subject_id>/sample_NNN_[full|thumb].jpg` for enrollments.

---

### quality_gate.py
Pre-recognition filter to reject low-quality faces and save storage/CompreFace API cost.

**Checks:**
- Min face size: 100×100 pixels
- Min blur score (Laplacian variance): 100.0

Returns tuple (bool, reason_string).

---

### face_cleanup.py
Scheduled retention-policy cleanup: delete unprocessed face detections older than the retention period.

**Protections:**
- Never deletes enrolled faces (is_enrolled=True)
- Skips detections with pending tasks or in PIT queue
- Batches 100 rows per cycle; idempotent on re-run

**Result:** CleanupResult with scanned, deleted, protected_by_task, protected_by_pit, errors counts.

---

### dedup.py (DedupCache)
In-memory 60-second sliding window to reject duplicate detections from the same face.

**Logic:**
- **Known subject** (subject_id set) → cache by subject_id; return True if seen in window
- **Unknown face** (subject_id=None) → perceptual hash (8×8 grayscale comparison); return True if hash match in window

Cleaned up periodically to prevent unbounded memory growth.

---

### enrollment.py (EnrollmentService)
Atomic enrollment: create FaceSample + register in CompreFace + update ComprefaceSubject row.

**Flow:**
1. Quality gate (422 if fails)
2. Verify contact exists (404 if not)
3. Resolve or create ComprefaceSubject (compreface_subject_id = "contact_<id>")
4. Guard against purged_at IS NOT NULL (409 if purged)
5. Write face crop to disk
6. Call CompreFace add_subject + add_example (transactional; best-effort rollback on fail)
7. Insert FaceSample row
8. Recompute sample_count (never blind-increment)

**Sources:** manual, detection, bulk_ingest, backfill.

---

---

## Contact & CRM Services

### contact_service.py
CRUD wrapper for Contact entity: fetch, create, update, delete with audit trail.

**Audit fields:** first_name, last_name, nickname, suffix, gender, birth_date, phone, email, street_address, contact_type, contact_subtype, custom_data.

**Features:**
- Display name resolution (uses nickname or first+last+suffix)
- Finds first enrolled thumbnail for contact
- Soft delete support (is_deleted flag)
- Event participation history

---

### name_match.py
4-stage Filipino-localized name-matching pipeline for bulk imports and manual name entry.

**Stages:**
1. **normalize_name(raw)** — sync, pure — strips diacritics, removes honorifics (tita/kuya/ate/pastor), handles "LASTNAME, Firstname" inversion, filters middle initials
2. **lookup_alias(normalized, db)** — exact NameAlias match (score=1.0, stage 3)
3. **lookup_deterministic(normalized, db)** — Jaro-Winkler fuzzy scoring with ambiguity rules (stage 4)
4. **lookup_claude(raw_name, normalized, candidates, db)** — Claude AI tiebreaker (rate-limited to 5 concurrent, stage 4b)

**Outcomes:** SINGLE (confident match), AMBIGUOUS (multiple candidates, enqueued for review), UNMATCHED (no candidates above threshold).

**Config:** Extensible nickname maps (Jose→Joe/Joey, Maria→Mary/Marites, etc.), honorific list, configurable Jaro-Winkler min_score (0.88 default).

---

### custom_fields.py
(Inferred) Custom field validation and coercion for Contact.custom_data JSON. Ensures data matches CustomFieldDef type/enum constraints.

---

---

## Migration & Backfill Services

### migration/runner.py
Orchestrator for 4-phase CiviCRM import pipeline: contacts → events → participants → links (with optional dry-run mode).

**Phases:**
- **contacts** — Upsert Contact rows; preflight validates custom_data against CustomFieldDef
- **events** — Upsert Event rows
- **participants** — Bulk upsert Participant rows via pre-loaded contact/event maps
- **links** — Resolve contact_external_id, call match_name for each row, write link or enqueue for review

**Dry-run mode:** Savepoints rolled back per chunk; ImportBatch/ImportRowResult remain committed for UI inspection.

**Post-import:** Calls member_status_service.recompute_all_contacts (CN-24) if available.

---

### migration/loader.py
(Inferred) Raw SQL/ORM write operations for bulk inserts/upserts during import phases.

**Exports:** upsert_contact, upsert_event, bulk_insert_participants, write_people_link.

---

### migration/mapper.py
(Inferred) Row-level transformation: map XLSX columns to Contact/Event/Participant ORM fields, apply type coercion, validation.

**Exports:** map_contact_row, map_event_row, map_participant_row, validate_column_map.

---

### migration/reader.py
(Inferred) XLSX/CSV row iterator with chunking; yields dicts keyed by column name.

---

### backfill.py (BackfillService)
Reconcile local ComprefaceSubject rows against live CompreFace subject list (e.g., after partial CF data loss or subject deletion).

**Steps:**
1. Fetch live CompreFace subject set
2. Load all DB ComprefaceSubject rows
3. Identify unregistered CF subjects (CF has them, DB does not) — logged only, no writes
4. For each DB subject, flag orphans (contact_id=NULL) and seed FaceSample rows from enrolled crops (if not already present)

**Idempotent:** image_path uniqueness check prevents re-run duplicates. Dry-run mode returns report without committing.

---

---

## Matching & Deduplication

### fr_transition.py
Remapping and backfill utilities for compreface_subject fields during FR system transition.

**remap_subjects()** — Idempotent bulk UPDATE to link legacy CiviCRM-named subjects (subject_name=member:N) to their current Contact rows via external_id map. Updates both compreface_subjects.contact_id and detection.matched_name.

**backfill_consent()** — For each active ComprefaceSubject, create BiometricConsent(consent_given=False, basis_note='pre-cutover-unknown') if none exists. Upsert AdminSetting enroll_without_consent=True.

**get_enroll_without_consent()** — Read current consent backfill state from AdminSetting.

---

---

## Queue & Worker Services

### queue_manager.py
(Inferred) Task queue manager: coordinates work distribution between distributed workers (queue_consumer.py instances).

---

### rtsp_capture.py (Worker)
Background long-running worker that captures RTSP streams from enabled cameras, detects faces, and routes through process_face_crop.

**Flow:**
1. Load Camera rows where enable_health_check=True
2. For each camera, spawn FFmpegCapture (async frame callback)
3. Per frame: CompreFace detect → spawn async tasks for each face
4. Call process_face_crop (quality gate → recognize → dedup → DB insert)
5. Periodically reload dynamic_settings (CompreFace URL, active_event_id)
6. Dedup cache cleanup every iteration

---

### queue_consumer.py (Worker)
Background async worker consuming task queue (created by RTSP capture and photo ingest) and running volunteer approval workflows.

**Uses:** QueueManager for work distribution, dynamic_settings for polling.

---

---

## Export & Audit Services

### export_service.py
Streaming and batch export of Contacts/Participants as CSV or constant-memory XLSX.

**Features:**
- Memory contract: never calls .all() on large sets; uses yield_per=1000 chunking
- Formula injection protection (prepends ' to cells starting with =, +, -, @, \t, \r)
- Column specs: 13 core + 7 derived + custom fields (ordered by group_id, weight)
- Filters: contact_type, contact_subtype, tier, is_active, is_regular, is_connected, audience_ids, date_from, date_to, etc.

**Exports:**
- stream_contacts_csv(db, params) → AsyncIterator[bytes]
- stream_participants_csv(db, params) → AsyncIterator[bytes]
- build_contacts_xlsx(db, params) → (path, row_count)
- build_participants_xlsx(db, params) → (path, row_count)

---

### audit.py
Minimal audit log writer: inserts AuditLog rows after primary mutations (called after flush, before commit).

**Contract:** actor_id=None signals system/background action. Caller is responsible for commit.

---

### bulk_service.py
Set-based bulk operations on Participant records (zero per-row Python iteration).

**Exports:**
- bulk_add_participants(db, req, caller_id) → BulkAddResult
- bulk_update_status(db, req, caller_id) → BulkStatusResult
- bulk_remove(db, req, caller_id) → BulkRemoveResult
- preview(db, req) → BulkPreviewResult
- bulk_upsert_participants(db, rows) → int — used by migration ETL

Each function commits independently (AC15 exception: bulk_upsert_participants delegates to caller).

---

### photo_ingest.py (PhotoIngestService)
Batch processing pipeline for uploaded images: detect faces → run through process_face_crop for each face.

**Features:**
- Maintains PhotoIngestBatch status + report list (capped at 500 entries)
- Per-image error recovery (decode, megapixel limit, detect failure)
- batch.processed_images flushed after each file for live polling
- Outcomes: auto_logged, tasked, skipped, deduplicated

---

---

## Architecture Patterns

**Async/await:** All I/O-bound operations use async. Type hints on all public functions.

**Error handling:** Services raise HTTPException with specific status codes (404, 409, 422, 502). Workers use try/except with logging and fallback.

**Transactions:** Core mutations wrapped in try/except; session.rollback() on failure. Audit logging called after flush, before commit.

**Circular imports (CN-25):** Services never import routers. Services import only from models, config, other services, SQLAlchemy, FastAPI, stdlib.

**Dialect branching:** PostgreSQL-specific SQL (UPDATE…FROM) vs. SQLite (correlated subquery) handled in _dialect() helpers (see bulk_service.py, name_match.py).

**Session ownership:** Caller owns session lifetime. Services accept AsyncSession and/or db_session_factory (asynccontextmanager).

---

## Related Notes

- [[Face Recognition Pipeline]] — detailed flow from RTSP capture to attendance logging
- [[Data Model]] — Contact, Detection, Participant, ComprefaceSubject, Task, Event schemas
- [[Home]] — project overview
