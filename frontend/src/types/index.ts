export interface User {
  id: number;
  email: string;
  name: string | null;
  role: 'volunteer' | 'admin';
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
  compreface_image_id: string | null;
  source: 'manual' | 'detection' | 'bulk_ingest' | 'backfill';
  quality_score: number | null;
  created_at: string;
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
