// S10 — Import Wizard TypeScript types
// Aligned to the frozen backend contracts in the sprint task.
// Heavy-batch types (ImportBatch, ImportBatchDetail, ImportRowResult, etc.)
// are re-exported from types/migration.ts to keep one source of truth.

export type { ImportBatch, ImportBatchDetail, ImportRowResult, ImportRowOutcome, ImportBatchStatus } from '@/types/migration';
export type { ImportBatchListResponse, ImportRowResultList } from '@/types/migration';

// ─── Upload / columns ────────────────────────────────────────────────────────

/** A single source column returned after file upload. */
export interface UploadColumn {
  /** The column header as it appears in the file. */
  name: string;
  /** First N sample values (may be empty strings). */
  sample: string[];
}

/** Response from POST /imports/upload */
export interface UploadResponse {
  batch_id: number;
  columns: UploadColumn[];
  sample_rows: Record<string, unknown>[];
  total_rows: number;
  encoding: string;
  delimiter: string;
  sheets: string[];
}

/** A suggested mapping entry returned by GET /imports/{id}/columns */
export interface SuggestedMapEntry {
  target: string;
  data_type: string;
  confidence: number;
}

/** A mappable target field (core field or custom field) */
export interface TargetField {
  key: string;
  label: string;
  data_type: string;
}

/** Response from GET /imports/{id}/columns?sheet= */
export interface ColumnsResponse {
  headers: string[];
  sample_rows: Record<string, unknown>[];
  sheets: string[];
  /** Fuzzy auto-suggested mapping: source header → suggestion */
  suggested_map: Record<string, SuggestedMapEntry>;
  /** Catalog of all valid target fields (core + active custom fields) */
  targets: TargetField[];
}

// ─── Preview ─────────────────────────────────────────────────────────────────

export interface PreviewCounts {
  would_create: number;
  would_update: number;
  would_skip: number;
  ambiguous: number;
  errors: number;
}

/** Response from POST /imports/{id}/preview */
export interface PreviewResponse {
  counts: PreviewCounts;
}

/** Request body for POST /imports/{id}/preview */
export interface ImportPreviewRequest {
  /** source header → target field key (or "ignore") */
  column_map: Record<string, string>;
  match_key: 'external_id' | 'email' | 'name';
  conflict_policy: 'skip' | 'update' | 'fill';
  sheet?: string;
  target_event_id?: number;
}

// ─── Run ─────────────────────────────────────────────────────────────────────

export interface RunCounts {
  created: number;
  updated: number;
  skipped: number;
  review: number;
  errors: number;
}

/** Response from POST /imports/{id}/run */
export interface RunResponse {
  counts: RunCounts;
  elapsed_ms: number;
  status: string;
}

/** Request body for POST /imports/{id}/run (same shape as preview) */
export type ImportRunRequest = ImportPreviewRequest;

// ─── Presets ─────────────────────────────────────────────────────────────────

export interface ImportPreset {
  id: number;
  name: string;
  entity: string;
  column_map: Record<string, string>;
  options: Record<string, unknown>;
  owner_id: number | null;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

export interface ImportPresetListResponse {
  items: ImportPreset[];
  total: number;
}

export interface ImportPresetCreate {
  name: string;
  entity: string;
  column_map: Record<string, string>;
  options: Record<string, unknown>;
  is_shared: boolean;
}

// ─── Wizard local state ───────────────────────────────────────────────────────

export type ImportEntity = 'contact' | 'participant';
export type MatchKey = 'external_id' | 'email' | 'name';
export type ConflictPolicy = 'skip' | 'update' | 'fill';
export type WizardStep = 1 | 2 | 3 | 4;

export interface WizardState {
  step: WizardStep;
  entity: ImportEntity;
  batchId: number | null;
  columns: UploadColumn[];
  sheets: string[];
  selectedSheet: string | null;
  /** source header → target key (or "ignore") */
  columnMap: Record<string, string>;
  matchKey: MatchKey;
  conflictPolicy: ConflictPolicy;
  targetEventId: number | null;
  previewCounts: PreviewCounts | null;
  runCounts: RunCounts | null;
  runElapsedMs: number | null;
  runBatchId: number | null;
}
