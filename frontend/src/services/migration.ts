import { api } from '@/services/api';
import type {
  ImportBatchListResponse,
  ImportBatchDetail,
  ImportRowResultList,
  MigrationSummary,
} from '@/types/migration';

export async function listBatches(params?: {
  entity?: string;
  mode?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ImportBatchListResponse> {
  const resp = await api.get<ImportBatchListResponse>('/migration/batches', { params });
  return resp.data;
}

export async function getBatch(batchId: number): Promise<ImportBatchDetail> {
  const resp = await api.get<ImportBatchDetail>(`/migration/batches/${batchId}`);
  return resp.data;
}

export interface ListRowsParams {
  outcome?: string;
  limit?: number;
  offset?: number;
}

export async function listRows(batchId: number, params: ListRowsParams = {}): Promise<ImportRowResultList> {
  const resp = await api.get<ImportRowResultList>(`/migration/batches/${batchId}/rows`, { params });
  return resp.data;
}

export async function downloadReport(batchId: number): Promise<void> {
  const resp = await api.get(`/migration/batches/${batchId}/report.csv`, {
    responseType: 'blob',
  });
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `migration_report_${batchId}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function getMigrationSummary(): Promise<MigrationSummary> {
  const resp = await api.get<MigrationSummary>('/migration/summary');
  return resp.data;
}
