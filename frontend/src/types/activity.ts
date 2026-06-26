import type { Paginated } from '@/types';

export type ActivityStatus = 'scheduled' | 'in_progress' | 'completed' | 'cancelled';
export type ActivityPriority = 'low' | 'normal' | 'high' | 'urgent';

/** Core Activity row — mirrors the backend ActivityResponse schema. */
export interface Activity {
  id: number;
  activity_type: string;
  subject: string;
  details: string | null;
  activity_date: string;
  due_date: string | null;
  status: ActivityStatus;
  priority: ActivityPriority;
  assignee_user_id: number | null;
  target_contact_id: number | null;
  created_by_id: number | null;
  completed_at: string | null;
  reminder_sent_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Resolved activity with display fields from joined tables. */
export interface ActivityDetail extends Activity {
  assignee_name: string | null;
  assignee_email: string | null;
  creator_name: string | null;
  target_contact_name: string | null;
}

export interface ActivityCreate {
  activity_type: string;
  subject: string;
  details?: string | null;
  activity_date?: string;
  due_date?: string | null;
  status?: ActivityStatus;
  priority?: ActivityPriority;
  assignee_user_id?: number | null;
  target_contact_id?: number | null;
}

export interface ActivityUpdate {
  activity_type?: string;
  subject?: string;
  details?: string | null;
  activity_date?: string;
  due_date?: string | null;
  status?: ActivityStatus;
  priority?: ActivityPriority;
}

export interface AssigneeOption {
  id: number;
  name: string | null;
  email: string;
  role: string;
}

export interface ActivityMeta {
  types: string[];
  statuses: string[];
  priorities: string[];
}

/** Paginated list of resolved activities. Re-uses the generic Paginated<T> from types/index.ts. */
export type PaginatedActivities = Paginated<ActivityDetail>;

export interface ActivityFilters {
  page?: number;
  page_size?: number;
  assignee_user_id?: number | string;
  target_contact_id?: number;
  status?: string;
  priority?: string;
  overdue?: boolean;
  sort?: string;
}
