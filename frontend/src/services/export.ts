// S05-F12: Export service — sync blob downloads + async job polling

import { api } from '@/services/api';
import type { ExportJobResponse } from '@/types/bulk';

/**
 * Trigger a browser download of a blob response.
 * Pass { responseType: 'blob' } to the axios call before calling this.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Perform a synchronous blob download directly from the API.
 * @param path    API path (e.g. '/export/contacts.csv')
 * @param params  Optional query params
 * @param filename  Suggested download filename
 */
export async function downloadBlobFromApi(
  path: string,
  params: Record<string, unknown> = {},
  filename: string,
): Promise<void> {
  const response = await api.get<Blob>(path, {
    params,
    responseType: 'blob',
  });
  downloadBlob(response.data, filename);
}

/**
 * Enqueue an async export job.
 * POST /export/jobs  { job_type, filters }
 */
export async function enqueueExportJob(
  jobType: string,
  filters: Record<string, unknown> = {},
): Promise<ExportJobResponse> {
  const response = await api.post<ExportJobResponse>('/export/jobs', {
    job_type: jobType,
    filters,
  });
  return response.data;
}

/**
 * Poll an export job by ID.
 * GET /export/jobs/{id}
 */
export async function pollExportJob(jobId: string): Promise<ExportJobResponse> {
  const response = await api.get<ExportJobResponse>(`/export/jobs/${jobId}`);
  return response.data;
}
