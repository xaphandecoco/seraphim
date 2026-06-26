// S30: CiviCRM migration TypeScript types — aligned to real backend schema

export type ImportBatchStatus = 'pending' | 'running' | 'completed' | 'failed';

/** What data entity the batch imported */
export type ImportEntity = 'contacts' | 'events' | 'participants' | 'links';

/** Whether the batch was a dry-run preview or a real live import */
export type ImportBatchMode = 'dry_run' | 'live';

export type ImportRowOutcome = 'created' | 'updated' | 'skipped' | 'error' | 'review';

export interface ImportBatchOut {
  id: number;
  source_filename: string | null;
  entity: string;
  mode: string;
  status: string;
  column_map: Record<string, unknown>;
  options: Record<string, unknown>;
  total_rows: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  error_count: number;
  review_count: number;
  started_at: string;
  finished_at: string | null;
  created_by_id: number | null;
}

/** Top-level alias used in page components */
export type ImportBatch = ImportBatchOut;

export interface ImportBatchDetail extends ImportBatchOut {
  pending_review_count: number;
}

export interface ImportBatchListResponse {
  items: ImportBatchOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface ImportRowResult {
  id: number;
  batch_id: number;
  row_number: number;
  external_id: string | null;
  outcome: ImportRowOutcome;
  entity_id: number | null;
  message: string | null;
  created_at: string;
}

export interface ImportRowResultList {
  items: ImportRowResult[];
  total: number;
  limit: number;
  offset: number;
}

export interface MigrationSummary {
  contacts: ImportBatchOut | null;
  events: ImportBatchOut | null;
  participants: ImportBatchOut | null;
  links: ImportBatchOut | null;
  pending_reviews: number;
}
