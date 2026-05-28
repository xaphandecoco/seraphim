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
  event_id: number;
  title: string;
  start_date: string;
  end_date?: string;
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
