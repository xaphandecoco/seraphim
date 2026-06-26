export interface User {
  id: number;
  email: string;
  name: string | null;
  role: 'volunteer' | 'admin' | 'viewer';
  is_active: boolean;
}

export interface Task {
  id: number;
  detection_id: number;
  tier: '100' | '91-99' | 'below90' | 'unknown';
  face_thumbnail_path: string | null;
  matched_name: string | null;
  confidence: number | null;
  camera_name: string | null;
  detected_at: string | null;
  status: string;
  required_approvals: number;
  current_approvals: number;
  skip_count: number;
  enrollment_progress: string | null;
}

export interface Member {
  contact_id: number;
  first_name: string;
  last_name: string;
  display_name?: string;
  email?: string;
}

export interface LeaderboardEntry {
  volunteer_id: string;
  volunteer_email: string;
  total_points: number;
  tasks_completed: number;
  accuracy_percent: number;
}

export interface ChurchEvent {
  id: number;
  title: string;
  start_at?: string;
  end_at?: string;
}

// ---------- Events ------------------------------------------------------------

export type EventType =
  | 'Sunday Celebration'
  | 'Prayer Meeting'
  | 'Powerhouse'
  | 'Community Meeting'
  | 'Conference'
  | 'Event';

export type SessionTime = '8AM' | '10AM' | '3PM';

export interface Event {
  id: number;
  external_id?: number | null;
  title: string;
  event_type?: string | null;
  session_time?: string | null;
  occurrence_date?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  location?: string | null;
  recurring_series_id?: number | null;
  is_active?: boolean | null;
  created_at?: string | null;
}

export interface ParticipantCounts {
  unique_count: number;
  total_count: number;
}

export interface EventDetail extends Event {
  participant_counts: ParticipantCounts;
}

export interface EventSeries {
  id: number;
  name: string;
  event_type: string;
  default_session_time?: string | null;
  default_location?: string | null;
  is_active: boolean;
  created_at?: string | null;
}

export interface EventParticipant {
  participant_id: number;
  contact_id?: number | null;
  contact_display_name?: string | null;
  status: string;
  source: string;
  role?: string | null;
  created_at: string;
}

export interface Camera {
  id: number;
  name: string;
  rtsp_url: string;
  zone_label: string | null;
  fps: number;
  enable_health_check: boolean;
  status: string;
}

export interface Attendee {
  contact_id: number;
  first_name: string;
  last_name: string;
  nickname?: string | null;
  email?: string | null;
  face_thumbnail_path?: string | null;
  sample_count: number;
}

export interface TaskEvent {
  type: 'task.created' | 'task.updated' | 'task.resolved' | 'queue.saturated' | 'safe_mode.changed';
  data?: Task;
  pending_count?: number;
}

export interface FaceSample {
  id: number;
  compreface_subject_id: string;
  contact_id: number | null;
  image_path: string;
  thumb_path: string | null;
  thumb_url?: string | null;
  compreface_image_id: string | null;
  source: 'manual' | 'detection' | 'bulk_ingest' | 'backfill';
  quality_score: number | null;
  created_at: string;
}

// ---------- Contacts ----------------------------------------------------------

export interface ContactReferenceChip {
  id: number;
  display_name: string;
  contact_type?: string | null;
}

export interface Contact {
  id: number;
  external_id?: string | null;
  first_name: string;
  last_name: string;
  nickname?: string | null;
  display_name: string;
  email?: string | null;
  phone?: string | null;
  contact_type?: string | null;
  contact_subtype?: string | null;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface ContactListItem {
  id: number;
  display_name: string;
  nickname?: string | null;
  contact_type?: string | null;
  contact_subtype?: string | null;
  tier?: 'tier0' | 'tier1' | 'tier2' | 'tier3' | 'inactive' | null;
  is_regular?: boolean | null;
  is_connected?: boolean | null;
  email?: string | null;
  phone?: string | null;
  face_thumbnail_path?: string | null;
  is_deleted?: boolean;
  last_attended_at?: string | null;
  attendance_count?: number | null;
  weeks_absent?: number | null;
}

export interface FaceSummary {
  enrolled: boolean;
  sample_count: number;
  thumb_url?: string | null;
}

export interface ContactDetail {
  id: number;
  display_name: string;
  first_name: string;
  last_name: string;
  nickname?: string | null;
  suffix?: string | null;
  contact_type?: string | null;
  contact_subtype?: string | null;
  gender?: string | null;
  birth_date?: string | null;
  phone?: string | null;
  email?: string | null;
  street_address?: string | null;
  external_id?: string | null;
  tier?: 'tier0' | 'tier1' | 'tier2' | 'tier3' | 'inactive' | null;
  is_active?: boolean | null;
  is_regular?: boolean | null;
  is_connected?: boolean | null;
  is_deleted?: boolean;
  custom_data?: Record<string, unknown>;
  contact_reference_chips?: ContactReferenceChip[];
  face?: FaceSummary | null;
  /** Computed from biometric_consent table. Only meaningful when face.enrolled is true. */
  consent_status?: 'none' | 'pending' | 'pre_cutover' | 'given';
  last_attended_at?: string | null;
  attendance_count?: number | null;
  weeks_absent?: number | null;
}

export interface DerivedBadges {
  tier: 'tier0' | 'tier1' | 'tier2' | 'tier3' | 'inactive' | null;
  is_active: boolean | null;
  is_regular: boolean | null;
  is_connected: boolean | null;
}

export interface ContactAttendanceItem {
  event_id: number;
  event_title: string;
  attended_at: string;
  source?: string | null;
  event_type?: string | null;
}

export interface Paginated<T> {
  total: number;
  page: number;
  page_size: number;
  items: T[];
}

export interface FacePanelData {
  subject_id: number | null;
  compreface_subject_id: string | null;
  enrollment_status: string | null;
  sample_count: number;
  is_orphan: boolean;
  purged_at: string | null;
  last_trained_at: string | null;
  samples: FaceSample[];
}

export interface PhotoIngestBatch {
  id: number;
  event_id: number | null;
  status: 'processing' | 'completed' | 'failed';
  total_files: number;
  processed_images: number;
  faces_found: number;
  tasks_created: number;
  errors: number;
  report: Record<string, unknown>[];
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

// ---------- FR Transition (T06) -------------------------------------------

export interface RemapSection {
  subjects_total: number;
  subjects_remapped: number;
  subjects_orphaned: number;
}

export interface ConsentSection {
  active_subjects_total: number;
  consent_rows_created: number;
  subjects_missing_consent: number;
  enroll_without_consent: boolean;
}

export interface SmokeTestSection {
  status: 'pass' | 'skip' | 'fail';
  detail: string;
}

export interface FRTransitionStatus {
  remap: RemapSection;
  participants_count: number;
  consent: ConsentSection;
  smoke_test: SmokeTestSection;
}

export interface RemapReport {
  subjects_total: number;
  subjects_remapped: number;
  subjects_skipped: number;
  subjects_orphaned: number;
  elapsed_seconds: number;
}

export interface ConsentBackfillReport {
  contacts_evaluated: number;
  rows_created: number;
  rows_skipped: number;
  elapsed_seconds: number;
}

export interface OrphanSubjectRow {
  id: number;
  subject_name: string;
  compreface_subject_id: string;
  enrollment_status: string;
}

export interface OrphanSubjectPage {
  items: OrphanSubjectRow[];
  total: number;
  page: number;
  page_size: number;
}

// ---------- S16 — Settings, System Status & Scheduled Jobs ------------------

export interface SettingItem {
  key: string;
  value: { value: any };
  category: string;
  description?: string;
  label?: string;
  requires_restart?: boolean;
  sensitive?: boolean;
}

export interface JobRun {
  id: number;
  job_name: string;
  status: 'running' | 'success' | 'completed' | 'failed' | 'skipped' | 'warning';
  detail: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface JobRunListResponse {
  items: JobRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface JobStatusItem {
  job_name: string;
  last_status: string | null;
  last_run_at: string | null;
}

export interface SystemStatus {
  overall: 'ok' | 'degraded' | 'down';
  services: {
    postgres: boolean;
    redis: boolean;
    compreface: boolean;
  };
  queue_depth: number;
  safe_mode: boolean;
  active_event_id: number | null;
  jobs: JobStatusItem[];
}

export interface ConfigChecklistItem {
  key: string;
  label: string;
  is_set: boolean;
  required: boolean;
}

export interface ConfigChecklistResponse {
  items: ConfigChecklistItem[];
  required_complete: boolean;
  recommended_complete: boolean;
  all_complete: boolean;
}
