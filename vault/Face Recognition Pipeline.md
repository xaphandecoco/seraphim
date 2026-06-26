# Face Recognition Pipeline

End-to-end flow for face detection, recognition, enrollment, and attendance logging in Project Seraphim.

---

## Overview

The face recognition pipeline processes a face crop from three sources:
1. **RTSP cameras** — continuous capture via FFmpeg, real-time processing
2. **Photo batch upload** — bulk image ingest endpoint
3. **Manual enrollment** — per-contact face sample registration

All three converge on the core `process_face_crop()` function, which applies a quality gate, queries CompreFace for recognition, caches dedup, logs Detection/Task records, and broadcasts SSE events.

---

## Source 1: RTSP Camera Capture → Recognition

### Step 1: Frame Capture (rtsp_capture.py)

**Trigger:** Django/FastAPI app starts rtsp_capture.py worker (background task).

**Setup:**
```
1. Load Camera rows from DB where enable_health_check=True
2. For each camera:
   - Instantiate FFmpegCapture (async RTSP stream reader)
   - Start reading frames (callback-driven)
3. Periodically reload dynamic_settings (every 60s) for:
   - CompreFace URL/API keys
   - Active event_id (set by admin in Settings UI)
```

**Frame flow:**
- FFmpeg decodes RTSP stream → numpy BGR array
- Per-frame callback invokes _on_frame(camera_id, frame, ctx)

### Step 2: Face Detection (CompreFace Detection API)

**Trigger:** _on_frame processes each frame.

**Process:**
1. Encode frame to JPEG bytes (cv2.imencode, quality=85)
2. Call `compreface.detect(image_bytes)` → list of face bounding boxes
   - Returns normalized coordinates: {x, y, w, h, probability}
   - Retry logic: 3 attempts, exponential backoff (2s, 4s, 8s)

**Output:** List of face bounding boxes (or empty if no faces detected).

### Step 3: Per-Face Async Tasks

**Trigger:** For each detected face_box, spawn `_process_face(camera_id, frame, face_box, ctx)` as async task.

**Context (PipelineContext):**
- `compreface` — ComprefaceClient instance
- `storage` — FaceStorage instance
- `dedup` — DedupCache instance

**Clamp box:** Ensure bounding box doesn't exceed frame bounds.

**Face crop:** Extract face_crop = frame[y:y+h, x:x+w] (BGR image).

### Step 4: Quality Gate Check (process_face_crop → FaceQualityGate)

**Checks:**
- Min face size: 100×100 pixels
- Min blur score: Laplacian variance ≥ 100.0

**On failure:**
- Log "Quality gate failed for camera X: <reason>"
- Save detection snapshot (thumb_path)
- Insert Detection row with status='skipped', tier='unknown'
- Return {"action": "skipped"}
- **No further processing**

**On success:** Continue to recognition.

### Step 5: CompreFace Recognition

**Trigger:** Quality gate passed.

**Process:**
1. Encode face_crop to JPEG bytes
2. Call `compreface.recognize(image_bytes)` → RecognitionResult
   - Returns best match: subject_id, similarity_score (0.0–1.0), box
   - Retry logic: same as detect

**Tier mapping:** (configurable via AdminSettings)
```
similarity >= high_threshold (0.98)        → Tier "100"
similarity >= medium_threshold (0.91)      → Tier "91-99"
similarity < 0.91                          → Tier "below90"
no match / null similarity                 → Tier "unknown"
```

**Output:** RecognitionResult with subject_id, similarity_score, tier.

---

## Core Processing: process_face_crop()

Located in **face_pipeline.py**. Orchestrates quality check → recognition → dedup → DB insertion → SSE broadcast.

### A. Quality Gate (already described above)

### B. Recognition (already described above)

### C. Dedup Check (DedupCache)

**Purpose:** Avoid processing the same face twice within 60-second window.

**Logic:**

**If subject_id is known (recognized member):**
```python
last_seen = cache._seen.get(subject_id)
if last_seen and (now - last_seen) < 60s:
    return {"action": "deduplicated"}  # Skip
cache._seen[subject_id] = now
```

**If subject_id is None (unrecognized):**
```python
face_hash = perceptual_hash(face_crop)  # 8x8 grayscale > mean
for prev_hash, prev_time in cache._unknown_hashes[-100:]:
    if prev_hash == face_hash and (now - prev_time) < 60s:
        return {"action": "deduplicated"}  # Skip
cache._unknown_hashes.append((face_hash, now))
```

**Dedup window:** 60 seconds (sliding). Cleanup every iteration.

### D. Save Detection Snapshot

**Trigger:** Dedup check passed or face quality already checked.

**Process (FaceStorage):**
1. Crop face from frame: face_crop = frame[y:y+h, x:x+w]
2. Generate filename: det_<timestamp>_<uuid>_[full|thumb].jpg
3. Save full-quality JPEG (85% quality)
4. Resize to 300×300 and save thumbnail (70% quality)
5. Return (full_path, thumb_path) relative to storage root

**Directory structure:**
```
STORAGE_PATH/
  faces/
    2026/01/15/
      det_1705336800_abc12345_full.jpg
      det_1705336800_abc12345_thumb.jpg
      det_1705336801_xyz67890_full.jpg
      ...
  enrolled/
    contact_42/
      sample_001_full.jpg
      sample_001_thumb.jpg
      sample_002_full.jpg
      ...
```

### E. Resolve Compreface Subject → Contact ID

**Query:** ComprefaceSubject.compreface_subject_id = subject_id

**If found:** member_id = subject.contact_id; matched_name = f"member:{member_id}"

**If not found:** member_id = None; matched_name = None.

### F. Insert Detection Row

**Row:**
```python
Detection(
    camera_id=camera_id,
    image_path=thumb_path,
    timestamp=now,
    compreface_subject_id=subject_id,
    confidence=similarity,
    tier=tier,
    status="auto_logged" if tier == "100" else "tasked",
    matched_name=matched_name,
    event_id=event_id,
)
```

**Status mapping:**
- Tier 100 → status='auto_logged'
- Tier 91-99 or below90 → status='tasked'
- Tier unknown + quality gate failed → status='skipped'

### G. Auto-Attendance Logging (Tier 100 only)

**Trigger:** tier == "100" AND member_id AND event_id.

**Dedup guard:** SELECT COUNT(*) WHERE (contact_id, event_id) to prevent IntegrityError on repeat visits.

**Write:**
```python
Participant(
    contact_id=member_id,
    event_id=event_id,
    detection_id=detection.id,
    status="attended",
    source="face",
)
```

**Warning:** If tier == 100 but event_id is None, log warning and skip attendance (requires active event).

### H. Create Task (Tiers 91-99 and below90)

**Trigger:** tier != "100".

**Approval count:**
- Tier 91-99 → required_approvals=1 (one volunteer confirms)
- Tier below90 → required_approvals=2 (two volunteers confirm)

**Row:**
```python
Task(
    detection_id=detection.id,
    status="pending",
    required_approvals=required_approvals,
    current_approvals=0,
    skip_count=0,
    skip_reasons=[],
)
```

### I. SSE Broadcast

**Trigger:** process_face_crop completes.

**Messages:**

**Tier 100 (auto-logged):**
```json
{"type": "pending_count", "pending_count": 0}
```

**Tier 91-99 / below90 (tasked):**
```json
{"type": "new_task", "tier": "91-99", "camera_id": 5}
```

**Clients listening:** Volunteer queue UI refreshes task list via Server-Sent Events.

---

## Source 2: Photo Batch Upload → Ingest

Located in **photo_ingest.py** (PhotoIngestService).

### Step 1: Batch Creation

**Endpoint:** POST /api/ingest/batch (creates PhotoIngestBatch row, status='pending')

**Payload:**
```python
{
    "event_id": 42,
    "files": [
        ("photo1.jpg", bytes),
        ("photo2.jpg", bytes),
        ...
    ]
}
```

### Step 2: Per-Image Processing

**For each (filename, raw_bytes):**

1. **Decode:** cv2.imdecode(nparr, IMREAD_COLOR) → numpy frame
2. **Size check:** Enforce 25 megapixel limit
3. **Encode for detect:** cv2.imencode(".jpg", frame) → image_bytes
4. **CompreFace detect:** Retrieve face bounding boxes
5. **Per face:** Call process_face_crop() (same as RTSP)

**Error recovery:**
- Decode failure → increment batch.errors, append report entry, continue
- Megapixel limit → increment batch.errors, continue
- Detect failure → increment batch.errors, continue
- process_face_crop failure → increment batch.errors, continue

**Counters:** batch.processed_images, batch.faces_detected, batch.auto_logged, batch.tasks_created, batch.skipped, batch.deduplicated, batch.errors.

**Reporting:** Report list capped at 500 entries; counters continue past cap.

### Step 3: Batch Status Update

**On success:** status='completed', finished_at=now.

**On unhandled exception:** status='failed', finished_at=now, then re-raise.

**Progress polling:** batch.processed_images incremented and committed after each file.

---

## Source 3: Manual Enrollment

Located in **enrollment.py** (EnrollmentService).

### Step 1: Quality Gate

**Checks:** Same as process_face_crop (min size, blur score).

**On failure:** Raise HTTPException(422, "Face quality check failed: <reason>").

### Step 2: Resolve or Create ComprefaceSubject

**Lookup:** SELECT FROM compreface_subjects WHERE compreface_subject_id = f"contact_{contact_id}".

**If not found:**
```python
ComprefaceSubject(
    subject_name=f"{first_name} {last_name}",
    compreface_subject_id=f"contact_{contact_id}",
    contact_id=contact_id,
    enrollment_status="pending",
    sample_count=0,
)
```

**Guard:** If subject.purged_at IS NOT NULL, raise HTTPException(409, "Subject has been purged").

### Step 3: Write Face Crop to Disk

**Call:** storage.save_enrollment(subject_id, face_crop) → (full_path, thumb_path).

### Step 4: CompreFace Registration (Atomic)

1. **add_subject(subject_id)** — ensure subject exists in CompreFace (idempotent)
2. **add_example(subject_id, image_bytes)** — upload face example; returns image_id
   - On None return → delete disk files (best-effort), raise HTTPException(502)
3. **Insert FaceSample row:**
   ```python
   FaceSample(
       compreface_subject_id=subject_id,
       image_path=full_path,
       thumb_path=thumb_path,
       source=source,  # "manual", "detection", "bulk_ingest", "backfill"
       compreface_image_id=image_id,
   )
   ```
4. **Recompute sample_count:** SELECT COUNT(*) FROM face_samples (never blind-increment)
5. **Update enrollment_status:** "active"
6. **Commit** (all-or-nothing; rollback on error)

**Rollback:** If any step fails after disk write, delete files (best-effort) and roll back session.

---

## CompreFace Integration

### Architecture

CompreFace is deployed as a separate microservice (Docker container). Project Seraphim connects via HTTP with API keys.

**Separate API keys:**
- **Detection key:** for POST /api/v1/detection/detect
- **Recognition key:** for POST /api/v1/recognition/* (subjects, faces, recognize)
- **Fallback:** Legacy single key (for backward compatibility)

### API Calls

**detect(image_bytes)**
```
POST /api/v1/detection/detect
Header: x-api-key: <detect_key>
Body: multipart/form-data {file: image.jpg}
Response: {
  "result": [
    {"box": {"x_min", "y_min", "x_max", "y_max"}, "probability": 0.95},
    ...
  ]
}
```

**recognize(image_bytes)**
```
POST /api/v1/recognition/recognize
Header: x-api-key: <recognize_key>
Body: multipart/form-data {file: image.jpg}
Response: {
  "result": [
    {
      "box": {...},
      "subjects": [
        {"subject": "contact_42", "similarity": 0.9856},
        {"subject": "contact_99", "similarity": 0.7423},
        ...
      ]
    }
  ]
}
```

**add_subject(subject_id)**
```
POST /api/v1/recognition/subjects
Body: {"subject": "contact_42"}
```

**add_example(subject_id, image_bytes)**
```
POST /api/v1/recognition/faces?subject=contact_42
Body: multipart/form-data {file: sample.jpg}
Response: {"image_id": "uuid-...-123"} or {"imageId": "uuid-...-123"}
```

**delete_example(image_id)**
```
DELETE /api/v1/recognition/faces/{image_id}
```

**list_subjects()**
```
GET /api/v1/recognition/subjects
Response: {"subjects": ["contact_42", "contact_99", ...]}
```

**delete_subject(subject_id)**
```
DELETE /api/v1/recognition/subjects/{subject_id}
```

### Retry Strategy

All API calls use tenacity retry decorator:
- **Stop:** 3 attempts
- **Wait:** exponential backoff (2s → 4s → 8s)
- **Retry on:** httpx.TimeoutException, httpx.ConnectError

---

## FR Transition & Consent Backfill

### remap_subjects()

Used when migrating from old CiviCRM external_id naming to new internal contact_id-based naming.

**Algorithm:**
1. Build mapping {external_id: contact.id} from Contact rows
2. Bulk UPDATE compreface_subjects.contact_id where subject_name matches "member:<external_id>" pattern
3. Bulk UPDATE detections.matched_name "member:<N>" → str(contact.id)
4. Identify orphaned subjects (contact_id IS NULL after remap)

**Returns:** RemapReport with subjects_remapped, subjects_orphaned, orphaned_subject_ids.

### backfill_consent()

Post-migration: create BiometricConsent records for previously-enrolled subjects (with consent_given=False, basis_note='pre-cutover-unknown').

**Steps:**
1. Fetch active ComprefaceSubjects with non-NULL contact_id
2. For each subject's contact, check if BiometricConsent row exists
3. If not, insert BiometricConsent(consent_given=False, basis_note='pre-cutover-unknown')
4. Upsert AdminSetting key='enroll_without_consent' to {'value': True}

**Returns:** ConsentBackfillReport with consent_rows_created, already_had_consent, enroll_without_consent_set.

---

## Task Resolution & Volunteer Approval

### Task Approval Flow

1. **Volunteer reviews Detection:** Opens task in volunteer queue UI
2. **Action: Confirm** → Increment current_approvals
   - If current_approvals >= required_approvals: status='approved'
   - Call task_service._log_attendance() → insert Participant row (same as auto-logged)
   - Award 1 point to volunteer
3. **Action: Edit (reassign)** → Change matched_name to different contact_id
   - Call task_service._log_attendance() for correct contact
   - Award 2 points to volunteer
4. **Action: Add (unidentified → identify)** → Link unidentified detection to contact
   - Set matched_name, tier (manually updated)
   - Call task_service._log_attendance()
   - Award 1 point to volunteer
5. **Action: Skip** → Increment skip_count; if > threshold, status='skipped'

### Attendance Logging

Triggered by:
- **Auto-log:** process_face_crop for tier 100
- **Task resolution:** volunteer confirms/edits/adds
- **Enrollment:** pit_queue enroll (PIT = Pending Identification Task)

All paths write Participant(contact_id, event_id, source='face') with dedup guard (contact_id, event_id) unique constraint.

---

## Cleanup & Retention

### Face Cleanup (Scheduled Job)

**Runs periodically** (e.g., daily). Deletes unprocessed face snapshots older than retention period (e.g., 30 days).

**Protected:** Enrolled faces (is_enrolled=True), pending tasks, PIT queue entries.

**Cleanup result:** CleanupResult with scanned, deleted, protected_by_task, protected_by_pit, errors counts.

### Dedup Cache Cleanup

**Runs every RTSP worker iteration.** Prunes entries older than 60-second window from both subject_id and unknown_hash caches.

---

## Performance & Reliability

### Concurrency

- **RTSP worker:** One async task spawned per face per frame; non-blocking
- **Photo ingest:** Per-image loop; process_face_crop called sequentially per face
- **Enrollment:** Single face per request; transactional

### Error Handling

- **Quality gate:** Silent skip; detection row created with status='skipped'
- **CompreFace detect:** Log warning; skip frame
- **CompreFace recognize:** Treat as "unknown" tier
- **Dedup:** Degrade to single-subject (known) if hash collision
- **DB constraint violation (auto-log):** Guard with SELECT before INSERT on (contact_id, event_id)

### Scaling

- **Stateless services:** Each service instance can scale horizontally
- **CompreFace bottleneck:** Single CompreFace instance; may need clustering for high-throughput
- **Storage I/O:** FaceStorage uses local filesystem; consider network-attached storage for distributed deployment

---

## Related Notes

- [[Backend Services]] — service module overview
- [[Data Model]] — Detection, Task, Participant, ComprefaceSubject, Event schemas
- [[Home]] — project overview
