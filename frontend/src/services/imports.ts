// S10 — Import Wizard service layer.
// All calls go through the shared `api` axios client (no /api prefix — nginx strips it).

import { api } from '@/services/api';
import type {
  UploadResponse,
  ColumnsResponse,
  PreviewResponse,
  RunResponse,
  ImportPreviewRequest,
  ImportRunRequest,
  ImportPreset,
  ImportPresetListResponse,
  ImportPresetCreate,
} from '@/types/imports';
import type { ImportBatchDetail, ImportRowResultList } from '@/types/migration';
import type { ImportBatchListResponse } from '@/types/migration';

// ─── File upload ─────────────────────────────────────────────────────────────

export async function uploadImport(file: File, entity: string): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('entity', entity);
  const resp = await api.post<UploadResponse>('/imports/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return resp.data;
}

// ─── Column mapping ───────────────────────────────────────────────────────────

export async function getColumns(batchId: number, sheet?: string): Promise<ColumnsResponse> {
  const resp = await api.get<ColumnsResponse>(`/imports/${batchId}/columns`, {
    params: sheet ? { sheet } : undefined,
  });
  return resp.data;
}

// ─── Preview + Run ────────────────────────────────────────────────────────────

export async function runPreview(
  batchId: number,
  req: ImportPreviewRequest,
): Promise<PreviewResponse> {
  const resp = await api.post<PreviewResponse>(`/imports/${batchId}/preview`, req);
  return resp.data;
}

export async function runImport(
  batchId: number,
  req: ImportRunRequest,
): Promise<RunResponse> {
  const resp = await api.post<RunResponse>(`/imports/${batchId}/run`, req);
  return resp.data;
}

// ─── Import batches list ──────────────────────────────────────────────────────

export async function listImportBatches(params?: {
  limit?: number;
  offset?: number;
}): Promise<ImportBatchListResponse> {
  const resp = await api.get<ImportBatchListResponse>('/imports', { params });
  return resp.data;
}

export async function getImportBatch(id: number): Promise<ImportBatchDetail> {
  const resp = await api.get<ImportBatchDetail>(`/imports/${id}`);
  return resp.data;
}

// ─── Row results ──────────────────────────────────────────────────────────────

export async function listImportRows(
  id: number,
  params?: { limit?: number; offset?: number; outcome?: string },
): Promise<ImportRowResultList> {
  const resp = await api.get<ImportRowResultList>(`/imports/${id}/rows`, { params });
  return resp.data;
}

export async function listPreviewRows(
  id: number,
  params?: { limit?: number; offset?: number; outcome?: string },
): Promise<ImportRowResultList> {
  const resp = await api.get<ImportRowResultList>(`/imports/${id}/preview-rows`, { params });
  return resp.data;
}

// ─── Report CSV download ──────────────────────────────────────────────────────

export async function downloadImportReport(batchId: number): Promise<void> {
  const resp = await api.get(`/imports/${batchId}/report.csv`, {
    responseType: 'blob',
  });
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `import_report_${batchId}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ─── Presets ─────────────────────────────────────────────────────────────────

export async function listPresets(entity?: string): Promise<ImportPresetListResponse> {
  const resp = await api.get<ImportPresetListResponse>('/imports/presets', {
    params: entity ? { entity } : undefined,
  });
  return resp.data;
}

export async function savePreset(preset: ImportPresetCreate): Promise<ImportPreset> {
  const resp = await api.post<ImportPreset>('/imports/presets', preset);
  return resp.data;
}

export async function updatePreset(
  id: number,
  preset: Partial<ImportPresetCreate>,
): Promise<ImportPreset> {
  const resp = await api.put<ImportPreset>(`/imports/presets/${id}`, preset);
  return resp.data;
}

export async function deletePreset(id: number): Promise<void> {
  await api.delete(`/imports/presets/${id}`);
}
