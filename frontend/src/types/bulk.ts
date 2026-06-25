// S05-F12: Bulk participant types — spec §5.1

/** Defines how the audience for a bulk operation is selected. */
export type AudienceMode = 'all' | 'group' | 'saved_search' | 'ids';

/** Payload sent to the preview and apply endpoints. */
export interface AudienceSelector {
  mode: AudienceMode;
  /** Required when mode === 'group' */
  group_id?: number;
  /** Required when mode === 'saved_search' */
  saved_search_id?: number;
  /** Required when mode === 'ids' */
  contact_ids?: number[];
}

/** Result returned by POST /participants/bulk-add, bulk-remove, bulk-set-status. */
export interface BulkParticipantResult {
  affected: number;
  skipped: number;
  errors: number;
}

/** Preview counts returned by POST /participants/bulk-preview. */
export interface BulkParticipantPreview {
  matched: number;
  already_registered?: number;
  will_add?: number;
  will_remove?: number;
  will_update?: number;
}

/** Export job returned by POST /export/jobs and GET /export/jobs/{id}. */
export type ExportJobStatus = 'pending' | 'running' | 'done' | 'failed';

export interface ExportJobResponse {
  id: string;
  status: ExportJobStatus;
  /** Download URL — populated when status === 'done' */
  download_url?: string | null;
  /** Human-readable error — populated when status === 'failed' */
  error?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}
