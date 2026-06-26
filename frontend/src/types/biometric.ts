// S08 — Biometric Consent & Right-to-be-Forgotten — TypeScript types

export type ConsentStatus = 'none' | 'pending' | 'given' | 'revoked' | 'purged';

export interface ConsentResponse {
  status: ConsentStatus;
  consent_given: boolean;
  consented_at: string | null;
  basis_note: string | null;
  retention_until: string | null;
  deletion_requested_at: string | null;
  purged_at: string | null;
  recorded_by_id: number | null;
  enrolled_photo_count: number;
  subject_active: boolean;
}

export interface PurgeDetail {
  files_deleted: number;
  samples_deleted: number;
  detections_cleared: number;
  compreface_deleted: boolean;
  errors: string[];
}

export interface PurgeResultResponse {
  status: 'purged';
  purge_detail: PurgeDetail;
}

export interface RetentionReportItem {
  contact_id: number;
  contact_name: string;
  consent_given: boolean;
  retention_until: string | null;
  deletion_requested_at: string | null;
  purged_at: string | null;
  status: string;
}

export interface RetentionReportResponse {
  items: RetentionReportItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ConsentRecordRequest {
  basis_note?: string;
  retention_years?: number;
}

export interface ConsentUpdateRequest {
  basis_note?: string;
  retention_until?: string;
}

export interface DeletionRequest {
  immediate?: boolean;
}

export interface RetentionReportParams {
  page?: number;
  page_size?: number;
  within_days?: number | null;
  include_purged?: boolean;
}
