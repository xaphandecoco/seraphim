# Data Model

SQLAlchemy 2.0 async models with Alembic migrations. All timestamps default to UTC-naive `datetime` via `utc_now()`. JSON columns use `JSONB` on PostgreSQL, `JSON` on SQLite (tests).

See [[Backend API]] for endpoint documentation.

## Auth & Users

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **users** | `id` (PK), `email` (unique), `password_hash`, `auth_provider` (local\|google), `role` (admin\|volunteer), `is_active`, `password_reset_token`, `password_reset_expires_at`, `created_at` | User accounts for login/admin; password reset tokens hashed; see [[Auth and Security]] for flow |

## Members & Contacts

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **contacts** | `id` (PK), `external_id` (unique, nullable — CiviCRM migration trace), `contact_type` (individual\|household\|organization), `contact_subtype` (nullable), `first_name`, `last_name`, `nickname`, `suffix`, `gender`, `birth_date`, `phone`, `email`, `street_address`, `custom_data` (JSONB), `is_deleted`, `created_at`, `updated_at` | App-minted contact record (replaces CiviCRM Member); core fields only per CN-16; partial unique index on `external_id` (NULL allowed) |
| | *Snapshot (nightly S23)* | `last_attended_at`, `attendance_count`, `weeks_absent`, `tier` (Tier0\|Tier1\|Tier2\|Tier3\|Inactive), `is_active`, `is_regular`, `is_connected` — all nullable, computed by job |

## Attendance & Events

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **events** | `id` (PK), `external_id` (unique, nullable — CiviCRM), `title`, `start_at`, `end_at`, `event_type` (Sunday Celebration, Prayer Meeting, Powerhouse, etc.), `session_time` (8AM\|10AM\|3PM), `occurrence_date`, `recurring_series_id` (FK → event_series, ondelete SET NULL), `is_active`, `location`, `created_at` | App-minted event; minimal core columns per CN-16; S04 adds series, session_time, occurrence_date |
| **event_series** | `id` (PK), `title`, `event_type`, `session_time`, `cadence` (JSONB — recurrence pattern), `default_location`, `is_active`, `created_at` | Recurring event series (e.g. Sunday Service, Powerhouse); cadence defaults to empty dict, populated by app layer |
| **participants** | `id` (PK), `contact_id` (FK → contacts, ondelete CASCADE), `event_id` (FK → events, ondelete CASCADE), `status` (attended\|registered\|no_show\|cancelled), `role` (nullable), `source` (face\|manual\|zoom\|name_list\|community_report\|import\|bulk\|migration), `detection_id` (FK → detections, nullable), `registered_by_id` (FK → users, nullable), `created_at` | Attendance record; unique constraint on (event_id, contact_id); multiple sources tracked for audit |

## Cameras & Face Recognition

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **cameras** | `id` (PK), `name`, `rtsp_url`, `zone_label`, `fps`, `enable_health_check`, `status` (streaming\|reconnecting\|offline), `offline_since`, `created_at` | Video sources; health-check enabled by default; tracks offline duration |
| **compreface_subjects** | `id` (PK), `subject_name`, `compreface_subject_id` (unique), `contact_id` (FK → contacts, nullable), `enrollment_status` (pending\|active), `sample_count`, `last_trained_at`, `enrollment_source` (nullable), `is_orphan`, `purged_at`, `_legacy_civicrm_contact_id` (nullable), `created_at` | CompreFace face enrollment mapping; S07 adds soft-delete + orphan tracking + backfill provenance |
| **face_samples** | `id` (PK), `compreface_subject_id` (FK → compreface_subjects), `contact_id` (FK → contacts, nullable), `image_path`, `thumb_path`, `compreface_image_id` (nullable), `source` (manual\|detection\|bulk_ingest\|backfill), `added_by_id` (FK → users, nullable), `quality_score`, `created_at` | One enrolled face image per subject; UUID tracks CompreFace server image; quality score nullable |
| **detections** | `id` (PK), `camera_id` (FK → cameras, nullable), `timestamp`, `image_path`, `confidence`, `tier` (100\|91-99\|below90\|unknown), `status` (auto_logged\|tasked\|skipped\|resolved\|expired\|pit), `matched_name`, `compreface_subject_id`, `event_id` (FK → events, nullable), `is_enrolled`, `deleted_at`, `_legacy_civicrm_event_id`, `created_at` | Raw facial detections from camera stream; maps to task or auto-logs attendance; soft-delete via `deleted_at` |
| **biometric_consent** | `id` (PK), `contact_id` (FK → contacts, ondelete CASCADE, unique), `consent_given`, `consented_at`, `basis_note`, `recorded_at` | Face enrollment consent gate; S24 stub — S08 extends with retention_until, deletion_requested_at |

## Audit & Task Queue

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **tasks** | `id` (PK), `detection_id` (FK → detections, ondelete CASCADE), `status` (pending\|confirmed\|resolved\|skipped\|expired\|pit), `required_approvals` (1\|2), `current_approvals`, `skip_count`, `skip_reasons` (JSONB list), `pit_status` (awaiting\|enrolled\|deleted\|non_person, nullable), `expiry_date` (default +31 days), `created_at` | Detection pending manual confirmation; two-tier approval support; 31-day expiry default |
| **task_actions** | `id` (PK), `task_id` (FK → tasks, ondelete CASCADE), `volunteer_id` (FK → users, nullable), `action` (confirm\|edit\|add\|skip\|admin_override\|audit_confirm\|audit_deny\|audit_edit), `reason`, `created_at` | Audit trail of task approvals; partial unique index: one approval action (confirm/edit/add) per volunteer per task, multiple skips allowed |
| **logs** | `id` (PK), `detection_id` (FK → detections, nullable), `face_snapshot_path`, `timestamp`, `camera_id` (FK → cameras, nullable), `matched_name`, `confidence`, `tier`, `action` (auto\|confirmed\|edited\|added\|skipped\|expired\|admin_override\|audit_confirmed\|audit_denied\|audit_edited), `volunteer_id` (FK → users, nullable), `second_volunteer_id` (FK → users, nullable), `event_id` (FK → events, nullable), `created_at` | Attendance action log with volunteer audit trail; snapshot of matched name + confidence at time of action |
| **audit_log** | `id` (PK), `actor_id` (FK → users, nullable — NULL for system/API-key), `action`, `entity`, `entity_id`, `before` (JSONB), `after` (JSONB), `created_at` | Centralized audit trail for all create/update/delete ops; S01 owned; indexes on (entity, entity_id), created_at |
| **pit_queue** | `id` (PK), `task_id` (FK → tasks, ondelete CASCADE), `admin_action` (enroll\|delete\|non_person, nullable), `admin_note` (nullable), `resolved_at` (nullable), `created_at` | Person-in-the-middle resolution queue for ambiguous detections |

## Volunteers & Analytics

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **volunteer_stats** | `volunteer_id` (FK → users, PK), `month` (YYYY-MM, PK), `tasks_confirmed`, `tasks_edited`, `tasks_added`, `accuracy_score` (default 100.0), `total_points` | Monthly volunteer leaderboard stats; composite PK (volunteer_id, month) |

## Photos & Uploads

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **photo_ingest_batches** | `id` (PK), `event_id` (FK → events, nullable), `uploaded_by_id` (FK → users, nullable), `status` (processing\|completed\|failed), `total_images`, `processed_images`, `faces_detected`, `auto_logged`, `tasks_created`, `skipped`, `deduplicated`, `errors`, `report` (JSONB list, ≤500 entries in-process), `created_at`, `finished_at` | Bulk face upload job; UI polls GET /uploads/photos/batch/{id} for live progress; report capped at 500 items |

## Settings & Configuration

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **admin_settings** | `key` (PK, String 100), `value` (JSONB, default dict), `category`, `description`, `requires_restart`, `sensitive`, `updated_at`, `updated_by` (FK → users, nullable) | Runtime-mutable settings (redis_url, compreface_url, jwt_secret, access_token_expire_minutes, etc.); loaded into DynamicSettings singleton on startup |

## Exports & Reporting

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **export_jobs** | `id` (PK), `job_type` (attendance\|contacts\|audit_log), `fmt` (csv\|xlsx), `params` (JSONB, arbitrary filters), `status` (pending\|running\|done\|error), `requested_by_id` (FK → users, nullable), `row_count`, `file_path`, `file_bytes`, `error`, `expires_at`, `created_at`, `finished_at` | Async export jobs; extensible job_type; params stored as JSONB for flexibility |

## Custom Fields

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **custom_field_group** | `id` (PK), `name` (snake_case, String 100), `label`, `entity` (contact\|event\|activity), `weight`, `is_active`, `created_at`, `updated_at` | Admin-defined field group; unique (entity, name); indexes on (entity, is_active, weight) |
| **custom_field_def** | `id` (PK), `group_id` (FK → custom_field_group, ondelete CASCADE), `name` (snake_case, String 100), `label`, `data_type` (text\|textarea\|select\|multiselect\|date\|number\|checkbox\|contact_reference), `options` (JSONB list of {value, label}), `is_required`, `is_multi`, `weight`, `is_active`, `help_text`, `created_at`, `updated_at` | Field definition; unique (group_id, name); __init__ ensures options=[] at construction time (pre-flush) |

## Name Matching (S22)

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **name_alias** | `id` (PK), `alias_text` (normalized lowercase), `alias_type` (nick\|typo\|alt_spelling\|maiden\|preferred), `contact_id` (FK → contacts, ondelete CASCADE), `created_by_id` (FK → users, nullable), `source` (admin\|bulk_import\|community_report\|inferred), `meta` (JSONB, nullable), `created_at`, `updated_at` | Canonical alias mapping for name-matching pipeline; unique index on alias_text; cascade delete on contact |
| **community_report** | `id` (PK), `event_id` (FK → events, nullable), `event_title` (free-text fallback), `submitted_by_id` (FK → users, nullable), `submitted_by_contact_id` (FK → contacts, nullable), `raw_text`, `parsed_names` (JSONB list), `zone`, `topics`, `prayer_items`, `remarks`, `attendee_names` (JSONB), `event_leader_name`, `event_leader_contact_id` (FK → contacts, nullable), `photo_paths` (JSONB), `match_status` (pending\|complete\|partial), `matched_count`, `review_count`, `status` (pending\|processing\|complete\|partial\|archived), `date_of_activity`, `created_at`, `updated_at` | Volunteer name-list submission; S22-F06 extended fields; all FK ondelete SET NULL for audit trail preservation |
| **name_match_review_queue** | `id` (PK), `community_report_id` (FK → community_report, nullable), `event_id` (FK → events, nullable), `contact_id` (FK → contacts, nullable), `raw_name`, `candidate_contact_id` (FK → contacts, nullable), `score` (Numeric 5,4), `status` (pending\|accepted\|rejected\|skipped), `resolved_by_id` (FK → users, nullable), `resolved_at`, `raw_payload` (JSONB, nullable), `source` (nullable), `created_at`, `updated_at` | Unresolved name-match candidates; becomes orphaned (community_report_id = NULL) if source deleted |

## Migrations & Imports (S30)

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **import_batch** | `id` (PK), `source_filename`, `entity` (contacts\|events\|participants\|links), `mode` (dry_run\|live), `status` (running\|done\|error\|partial), `column_map` (JSONB mapping source → app field), `options` (JSONB), `total_rows`, `created_count`, `updated_count`, `skipped_count`, `error_count`, `review_count`, `started_at`, `finished_at`, `created_by_id` (FK → users, nullable) | Spreadsheet/CSV import tracking; mode indicates run kind (not write-op type); user row removal doesn't cascade delete batch |
| **import_row_result** | `id` (PK), `batch_id` (FK → import_batch, ondelete CASCADE), `row_number`, `external_id` (String 64 — raw token), `outcome` (created\|updated\|skipped\|error\|review), `entity_id` (matched/created Contact/Event/Participant), `message`, `raw` (JSONB capped), `created_at` | Per-row outcome; composite index (batch_id, outcome) for batch-scoped filtering |

## Indexes & Constraints

Key indexes for query performance:
- **contacts**: `ix_contacts_external_id` (partial unique, NULL allowed), `ix_contacts_email`, `ix_contacts_last_name`, `ix_contacts_is_deleted`
- **events**: `ix_events_external_id` (partial unique), `ix_events_start_at`, `ix_events_event_type`, `ix_events_occurrence_date`
- **participants**: `ix_participants_contact_id`, `ix_participants_event_id`, `ix_participants_source`; unique (event_id, contact_id)
- **task_actions**: `uq_task_action_approval` (partial unique on approval actions per volunteer per task, partial predicate on action type)
- **audit_log**: `ix_audit_log_entity_entity_id`, `ix_audit_log_created_at`
- **face_samples**: `ix_face_samples_contact_id`, `ix_face_samples_subject_id`
- **community_report**: `ix_community_report_event_id`, `ix_community_report_status`, `ix_community_report_submitted_by_id`, `ix_community_report_match_status`, `ix_community_report_zone`
- **name_match_review_queue**: `ix_nmrq_community_report_id`, `ix_nmrq_event_id`, `ix_nmrq_contact_id`, `ix_nmrq_status`, `ix_nmrq_candidate_contact_id`, `ix_nmrq_source`
- **export_jobs**: `ix_export_jobs_status_created`, `ix_export_jobs_requested_by_id`
- **custom_field_group**: `ix_cfg_entity_active_weight`
- **custom_field_def**: `ix_cfd_group_active_weight`

## Alembic Migrations

All migrations live in `backend/alembic/versions/`. Never edit after applying. Key migration milestones:
- **74e9ab60ea7e_initial**: Core schema (users, cameras, contacts, events, tasks, logs, audit_log)
- **g7h8i9j0k1l2_schema_inversion_civicrm_excision**: S06 — CiviCRM removal; external_id migration trace
- **h1i2j3k4l5m6_add_custom_field_engine**: Custom field framework
- **i3j4k5l6m7n8_s07_face_enrollment_and_samples**: Face enrollment, CompreFace subjects, FaceSamples
- **a2b3c4d5e6f7_s05_export_jobs**: Export job tracking
- **b1c2d3e4f5a6_s22_name_matching_tables**: Name aliases, community reports, review queue
- **j4k5l6m7n8o9_s04_event_series_and_event_columns**: EventSeries, session_time, occurrence_date, recurring_series_id
- **eed28c4ef46a_add_migration_etl_tables**: ImportBatch, ImportRowResult
- Others: nickname, dead letter queue fields, detection soft-delete, performance indexes, task action approval unique constraint

---
**Related:**
- [[Backend API]] — endpoint documentation
- [[Backend Services]] — worker jobs (S23 snapshot recompute, face training, etc.)
- [[Home]] — project overview
