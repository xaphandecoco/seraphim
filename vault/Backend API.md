# Backend API

REST API for [[Project Seraphim]] — facial-recognition church attendance system. Built with FastAPI; nginx strips `/api` prefix at ingress.

## Overview

**Base URL (after nginx proxy):** `/api` (stripped by nginx; FastAPI routes have no prefix)
**Authentication:** Bearer tokens (access token in Authorization header) or HttpOnly refresh cookie
**Response Format:** JSON
**Error Handling:** HTTP status codes + `{"detail": "..."}` error messages

---

## Auth Router

Manages user authentication, authorization, and session lifecycle. Implements JWT-based access/refresh token rotation with optional Google OAuth.

- **POST** `/auth/login` — Local email/password login; returns access token, sets HttpOnly refresh cookie
- **POST** `/auth/logout` — Clears refresh cookie, deny-lists JTI
- **GET** `/auth/me` — Current user profile (requires auth)
- **POST** `/auth/refresh` — Issue new access token + rotate refresh token
- **POST** `/auth/reset-password` — Admin requests password reset; generates secure reset link
- **POST** `/auth/reset-password/confirm` — Volunteer confirms password reset with token
- **GET** `/auth/users` — Admin: list all user accounts
- **POST** `/auth/add-volunteer` — Admin: create new volunteer account
- **POST** `/auth/deactivate/{user_id}` — Admin: deactivate user (retains history)
- **GET** `/auth/config` — Public: returns auth feature flags (e.g., Google OAuth enabled)
- **GET** `/auth/google` — Initiate Google OAuth flow with PKCE
- **GET** `/auth/google/callback` — Google OAuth callback; redirects to frontend with token

---

## Members Router

Contact/member management — create, search, view, update, soft-delete, and restore contacts. Includes attendance history lookup.

- **GET** `/members` — Paginated contact search (filters: search, contact_type, subtype, tier, is_regular, include_deleted)
- **GET** `/members/attendees` — List all contacts with enrolled face thumbnails (S07 spec)
- **POST** `/members` — Create new contact
- **GET** `/members/{contact_id}` — Get contact detail (includes face summary, badges, custom fields)
- **PATCH** `/members/{contact_id}` — Partial update contact
- **DELETE** `/members/{contact_id}` — Soft-delete contact (idempotent)
- **POST** `/members/{contact_id}/restore` — Admin: restore soft-deleted contact (returns 409 if not deleted)
- **GET** `/members/{contact_id}/attendance` — Paginated attendance history for contact

---

## Attendance Router

Name-list intake and bulk attendance recording. Runs name matching pipeline (alias + deterministic + optional Claude) and inserts matched contacts as Participant rows.

- **POST** `/attendance/name-list` — Accept list of attendee names, match to contacts, record attendance (supports large batches with async Claude dispatch)
- **GET** `/attendance` — List participant records with optional filters (event_id, date, status)

---

## Events Router

Event CRUD, participant management, and active-event tracking. Routes are order-sensitive to prevent path parameter shadowing.

- **GET** `/events/active-event-id` — Return currently active event ID (or null if none set)
- **POST** `/events/set-active` — Admin: set or clear active event (detections tagged with this event_id)
- **GET** `/events` — List events with filters (event_type, date_from, date_to, series_id, is_active); paginated
- **POST** `/events` — Admin: create new event
- **GET** `/events/{event_id}` — Get event detail (includes participant counts)
- **PATCH** `/events/{event_id}` — Admin: partial update event
- **DELETE** `/events/{event_id}` — Admin: soft-delete event (sets is_active=false)
- **GET** `/events/{event_id}/participants` — List participants for an event; paginated
- **POST** `/events/{event_id}/participants` — Add participant to event (source='manual')
- **PATCH** `/events/{event_id}/participants/{participant_id}` — Update participant status (and optionally role)

---

## Event Series Router

Recurring event series CRUD and generation. Bridges series definitions to individual Event instances.

- **GET** `/event-series` — List event series; paginated
- **POST** `/event-series` — Admin: create new event series
- **GET** `/event-series/{series_id}` — Get series detail
- **PATCH** `/event-series/{series_id}` — Admin: partial update series
- **DELETE** `/event-series/{series_id}` — Admin: soft-delete series
- **POST** `/event-series/{series_id}/generate` — Admin: generate events from series for a target date

---

## Cameras Router

Camera configuration and management for facial-recognition system.

- **GET** `/cameras` — Admin: list all cameras
- **POST** `/cameras` — Admin: create new camera (RTSP URL, FPS, zone label, health check flag)
- **PUT** `/cameras/{camera_id}` — Admin: update camera settings
- **DELETE** `/cameras/{camera_id}` — Admin: delete camera
- **GET** `/cameras/{camera_id}/preview` — Admin: grab single preview frame from camera

---

## Enrollment Router

Face sample management and recognition history. Two routers: `/contacts` (contact-scoped) and `/enrollment` (backfill operations).

**Contact-scoped (`/contacts` prefix):**
- **POST** `/contacts/{contact_id}/faces` — Upload face image for contact, enroll in CompreFace; returns FaceSampleResponse (201)
- **GET** `/contacts/{contact_id}/faces` — List face samples for contact
- **DELETE** `/contacts/{contact_id}/faces/{sample_id}` — Admin: delete face sample
- **GET** `/contacts/{contact_id}/recognition-history` — Recognition history for contact (paginated)
- **POST** `/contacts/{contact_id}/retrain` — Trigger face model retraining after updates

**Backfill (`/enrollment` prefix):**
- **POST** `/enrollment/backfill` — Admin: batch-import face samples from staging directory or archive
- **GET** `/enrollment/backfill-status` — Check status of ongoing backfill job

---

## Analytics Router

Admin reporting on attendance, volunteer performance, queue health, and detection tiers.

- **GET** `/analytics/attendance-by-event` — Attendance count per event (last 20 events)
- **GET** `/analytics/tier-distribution` — Count of detections by confidence tier
- **GET** `/analytics/volunteer-stats` — Aggregated volunteer performance metrics (tasks, points, accuracy)
- **GET** `/analytics/queue-health` — Daily detection/resolution counts (last 14 days)

---

## Audit Router

Quality verification tasks for enrolled faces. Volunteers review and confirm matches; actions feed into accuracy metrics.

- **GET** `/audit/tasks` — Get enrolled detections for volunteer audit review (surfaces when main queue empty)
- **POST** `/audit/tasks/{detection_id}/confirm` — Volunteer confirms match
- **POST** `/audit/tasks/{detection_id}/deny` — Volunteer rejects match
- **POST** `/audit/tasks/{detection_id}/correct` — Volunteer corrects match to different contact

---

## Setup Router

Initial system setup: connection testing, service validation, and bootstrap configuration.

- **GET** `/setup/status` — Check if initial setup has been completed
- **POST** `/setup/test-connection` — Test Postgres and Redis connections
- **POST** `/setup/test-services` — Test CompreFace and CiviCRM connectivity
- **POST** `/setup/complete` — Admin: finalize setup, create bootstrap config, lock endpoints

---

## Health Router

System health checks and queue status monitoring.

- **GET** `/health` — Full health check (DB, Redis, CompreFace status + overall status)
- **GET** `/health/queue` — Queue saturation status and safe-mode flag (volunteer-readable)

---

## Tasks Router

Main task queue for facial-recognition verification. Volunteers confirm, edit, or skip unidentified face detections.

- **GET** `/tasks` — Paginated task list (filters: status, page, page_size)
- **GET** `/tasks/{task_id}` — Get task detail
- **POST** `/tasks/{task_id}/confirm` — Volunteer confirms face match
- **POST** `/tasks/{task_id}/edit` — Volunteer corrects match to different contact
- **POST** `/tasks/{task_id}/skip` — Volunteer skips task with optional reason
- **POST** `/tasks/{task_id}/unmatched` — Mark detection as unmatched/unidentifiable
- **GET** `/tasks/feed` — SSE endpoint for real-time task queue updates (Bearer token or HttpOnly cookie)

---

## Leaderboard Router

Volunteer performance rankings by points and task completion.

- **GET** `/leaderboard` — Ranked list of volunteers; filters: period (this_month, previous_month, all_time)

---

## Logs Router

Audit and system logging viewer. Admin-only access.

- **GET** `/logs` — Filtered log viewer (filters: start_date, end_date, camera_id, event_id, volunteer_id, action, tier); paginated

---

## Settings Router

Admin settings CRUD. Masked sensitive values; protected keys cannot be blanked.

- **GET** `/settings` — Admin: read all settings (sensitive values masked)
- **PUT** `/settings` — Admin: update settings; reloads in-memory cache
- **DELETE** `/settings/{key}` — Admin: delete setting (protected keys cannot be deleted)

---

## Uploads Router

Face image uploads and photo batch ingest for bulk processing.

- **POST** `/uploads/faces` — Upload image, detect faces, run recognition pipeline (multi-face support)
- **POST** `/uploads/photo-ingest` — Batch ingest photos from file archive or directory
- **GET** `/uploads/photo-ingest/jobs` — List photo ingest jobs (paginated)
- **GET** `/uploads/photo-ingest/jobs/{job_id}` — Get ingest job detail and results

---

## Storage Router

Authenticated file serving for enrolled faces and generated exports. Supports Bearer token, query-param token, or HttpOnly refresh cookie.

- **GET** `/storage/{file_path:path}` — Serve file (requires auth; access tokens block refresh tokens per S1/Design B)

---

## Export Router

Streaming CSV exports and async XLSX job creation for contacts and participants.

- **GET** `/export/contacts.csv` — Stream contacts as CSV (viewer+)
- **GET** `/export/contacts.xlsx` — Build contacts XLSX, return FileResponse
- **GET** `/export/participants.csv` — Stream participants as CSV (viewer+)
- **GET** `/export/participants.xlsx` — Build participants XLSX, return FileResponse
- **POST** `/export/jobs` — Admin: create async export job (contacts or participants)
- **GET** `/export/jobs` — List own export jobs (admin sees all); newest first
- **GET** `/export/jobs/{job_id}` — Get export job detail (download_url only when status='ready')
- **GET** `/export/jobs/{job_id}/download` — Download completed export file

---

## Migration Router

CiviCRM import batch inspection and management. Streaming CSV report export.

- **GET** `/migration/batches` — Admin: paginated list (filters: entity, mode, status)
- **GET** `/migration/batches/{batch_id}` — Admin: batch detail (includes pending_review_count)
- **GET** `/migration/batches/{batch_id}/rows` — Admin: paginated row results (filter: outcome)
- **GET** `/migration/batches/{batch_id}/report.csv` — Admin: streaming CSV download
- **GET** `/migration/summary` — Admin: latest completed batch per entity + total pending reviews

---

## Participants Router

Bulk participant operations — enroll, update status, and remove contacts in batches.

- **POST** `/participants/bulk-add` — Bulk-enroll audience in event (AudienceSelector: by_tier, by_type, by_group, all)
- **POST** `/participants/bulk-status` — Bulk-update participant status (optional: only_if_status condition)
- **POST** `/participants/bulk-remove` — Bulk-remove (soft cancel or hard delete; hard requires admin)
- **POST** `/participants/bulk-preview` — Count-only preview, no writes

---

## Community Reports Router

Volunteer reporting on community events, attendee names, and event leaders. Auto-resolves submitter with name matching.

- **POST** `/community-reports` — Volunteer: submit report (auto-resolves submitter, runs name matching)
- **GET** `/community-reports` — Paginated list (filters: event_id, status, date_from, date_to)
- **GET** `/community-reports/{id}` — Volunteer: detail (includes review_queue_items + matched_contacts)
- **PATCH** `/community-reports/{id}` — Admin: partial update (matches, status)
- **DELETE** `/community-reports/{id}` — Admin: soft-delete (sets status='archived')
- **POST** `/community-reports/{id}/process` — Admin: re-trigger name matching on report attendees

---

## Custom Fields Router

Custom field schema definition and validation. Supports groups and typed fields.

- **GET** `/custom-fields/schema` — Get active groups and fields for entity (weight-ordered)
- **GET** `/custom-fields/groups` — Admin: list custom field groups
- **POST** `/custom-fields/groups` — Admin: create field group (immutable: name, entity after create)
- **PATCH** `/custom-fields/groups/{id}` — Admin: update group (rejects mutation of name/entity)
- **DELETE** `/custom-fields/groups/{id}` — Admin: hard delete group (409 if contacts use fields in group)
- **GET** `/custom-fields/defs` — List field definitions for entity
- **POST** `/custom-fields/defs` — Admin: create field definition (immutable: name, data_type after create)
- **PATCH** `/custom-fields/defs/{id}` — Admin: update field (rejects mutation of name/data_type)
- **DELETE** `/custom-fields/defs/{id}` — Admin: hard delete field (409 if contacts have custom_data values)
- **POST** `/custom-fields/validate` — Validate custom_data object against schema

---

## Name Aliases Router

Name alias management for name-matching fallback lookups.

- **GET** `/name-aliases` — Admin: search + paginate aliases
- **POST** `/name-aliases` — Admin: create alias (auto-normalized)
- **POST** `/name-aliases/teach` — Volunteer: teach alias from review-queue resolution
- **PATCH** `/name-aliases/{id}` — Admin: update (swap contact_id only)
- **DELETE** `/name-aliases/{id}` — Admin: hard delete; returns 204

---

## Name Match Router

Review queue for ambiguous/unmatched names from name-matching pipeline. Resolution workflow and re-processing.

- **GET** `/name-match/review-queue` — Paginated list (filters: status, source, event_id)
- **POST** `/name-match/review-queue/{id}/resolve` — Accept match + optional teach_alias
- **POST** `/name-match/review-queue/{id}/unmatch` — Reject match (sets status='rejected')
- **POST** `/name-match/review-queue/{id}/skip` — Leave pending; idempotent
- **POST** `/name-match/reprocess-aliases` — Admin: re-run pipeline on pending rows with updated aliases

---

## FR Transition Router

Facial-recognition transition verification and operations (T03). Subject→contact remap, consent backfill, smoke testing.

- **GET** `/admin/fr-transition/status` — Full transition health summary (remap, consent, smoke test sections)
- **POST** `/admin/fr-transition/remap` — Trigger subject→contact remap (orphan cleanup)
- **POST** `/admin/fr-transition/consent-backfill` — Backfill BiometricConsent rows from enrollment history

---

## Pit Router

Admin pit queue for unidentified/low-confidence face detections requiring manual enrollment.

- **GET** `/pit` — List all pit queue tasks (status='pit')
- **POST** `/pit/{task_id}/enroll` — Enroll pit face to contact (moves task to enrolled)
- **POST** `/pit/{task_id}/discard` — Discard pit face (removes from queue)

---

## Related Notes

- [[Backend Services]] — Business logic and data processing
- [[Data Model]] — SQLAlchemy ORM schema, migrations, relationships
- [[Home]] — Project overview

