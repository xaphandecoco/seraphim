// S22: Name-matching + community-report TypeScript types

export type MatchOutcome = 'matched' | 'ambiguous' | 'unmatched';
export type MatchMethod = 'alias' | 'fuzzy_forward' | 'fuzzy_reversed' | 'fuzzy_nickname' | 'claude';
export type ReviewQueueStatus = 'pending' | 'matched' | 'unmatched' | 'skipped';

export interface MatchCandidate {
  contact_id: number;
  display_name: string;
  score: number;
  method: MatchMethod;
}

export interface NameMatchResultItem {
  raw_name: string;
  normalized: string;
  outcome: MatchOutcome;
  contact_id: number | null;
  score: number | null;
  method: MatchMethod | null;
  review_queue_id: number | null;
}

export interface NameListIntakeResponse {
  total: number;
  matched: number;
  skipped_existing: number;
  review_queue: number;
  claude_pending?: boolean;
  results: NameMatchResultItem[];
}

export interface ReviewQueueItem {
  id: number;
  raw_name: string;
  normalized_name: string;
  source: string;
  status: ReviewQueueStatus;
  event_id: number | null;
  event_title: string | null;
  community_report_id: number | null;
  candidates: MatchCandidate[];
  resolved_contact_id: number | null;
  resolved_contact_name: string | null;
  resolved_by_id: number | null;
  resolve_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedReviewQueueResponse {
  items: ReviewQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReviewQueueResolveRequest {
  contact_id: number;
  teach_alias?: boolean;
  alias_text?: string;
}

export interface NameAlias {
  id: number;
  alias_text: string;
  contact_id: number;
  contact_display_name?: string;
  created_by_id: number | null;
  created_at: string;
}

export interface NameAliasTeachRequest {
  review_queue_id: number;
  alias_text?: string;
}

export type CommunityReportStatus = 'submitted' | 'pending' | 'processing' | 'complete' | 'partial' | 'archived';

export interface CommunityReport {
  id: number;
  event_id: number | null;
  event_title: string | null;
  zone: string | null;
  date_of_activity: string;
  submitted_by_id: number | null;
  submitted_by_name: string | null;
  status: CommunityReportStatus;
  match_status: string;
  matched_count: number;
  review_count: number;
  attendee_count: number | null;
  created_at: string;
}

export interface CommunityReportDetail extends CommunityReport {
  attendee_names: string[];
  event_leader_name: string | null;
  topics: string | null;
  prayer_items: string | null;
  remarks: string | null;
  photo_paths: string[];
  review_queue_items: ReviewQueueItem[];
  matched_contacts: Array<{ contact_id: number; display_name: string }>;
}

export interface CommunityReportCreate {
  event_id?: number | null;
  event_title?: string | null;
  zone?: string | null;
  date_of_activity: string;
  attendee_count?: number | null;
  attendee_names?: string[];
  event_leader_name?: string | null;
  topics?: string | null;
  prayer_items?: string | null;
  remarks?: string | null;
  photo_paths?: string[];
}

export interface PaginatedCommunityReportResponse {
  items: CommunityReport[];
  total: number;
  page: number;
  page_size: number;
}
