// S11 — Find & Merge Duplicates — TypeScript types.
// Aligned to the frozen backend contracts in the sprint task.

export interface ContactLite {
  id: number;
  first_name: string;
  last_name: string;
  email: string | null;
  phone: string | null;
  participant_count: number;
  face_sample_count: number;
  contact_type?: string;
  birth_date?: string | null;
  external_id?: number | null;
}

export interface CandidatePair {
  contact_a: ContactLite;
  contact_b: ContactLite;
  score: number;
  matched_fields: string[];
  same_name: boolean;
}

export interface CandidatePairList {
  items: CandidatePair[];
  total: number;
  page: number;
  page_size: number;
}

export interface CandidateRunRequest {
  rule_set_id?: number;
  contact_type?: string;
  max_pairs?: number;
  page?: number;
  page_size?: number;
}

export interface FieldConflictValue {
  survivor: unknown;
  loser: unknown;
  differs: boolean;
}

export interface ReassignmentCounts {
  reassigned: number;
  deleted: number;
}

export interface MergePreviewResponse {
  field_conflicts: Record<string, FieldConflictValue>;
  custom_field_conflicts: Record<string, FieldConflictValue>;
  reassignments: Record<string, ReassignmentCounts>;
  same_name: boolean;
  warnings: string[];
}

export interface MergeExecuteRequest {
  survivor_id: number;
  loser_id: number;
  confirm_same_name: boolean;
  field_choices?: Record<string, 'survivor' | 'loser'>;
}

export interface MergeExecuteResponse {
  survivor_id: number;
  reassignments: Record<string, unknown>;
  warnings: string[];
}

export interface MergeHistoryItem {
  id: number;
  survivor_id: number;
  loser_snapshot?: unknown;
  created_at: string;
  actor?: unknown;
  [key: string]: unknown;
}

export interface MergeHistoryList {
  items: MergeHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface DedupeFieldRule {
  field: string;
  weight: number;
  length?: number | null;
}

export interface RuleSet {
  id: number;
  name: string;
  is_default: boolean;
  is_active: boolean;
  threshold: number;
  rules: DedupeFieldRule[];
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface RuleSetCreate {
  name: string;
  description?: string | null;
  rules: DedupeFieldRule[];
  threshold: number;
  is_default?: boolean;
  is_active?: boolean;
}

export type RuleSetUpdate = Partial<RuleSetCreate>;

export const DEDUPE_FIELD_WHITELIST = [
  'first_name',
  'last_name',
  'suffix',
  'gender',
  'birth_date',
  'phone',
  'email',
  'street_address',
] as const;

export type DedupeWhitelistField = (typeof DEDUPE_FIELD_WHITELIST)[number];
