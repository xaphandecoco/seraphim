import { api } from './api';
import type {
  SystemStatus,
  ConfigChecklistResponse,
  JobRunListResponse,
} from '@/types';

export const REGISTERED_JOBS = [
  'sunday_generation',
  'powerhouse_generation',
  'eow_recompute',
  'eom_recompute',
  'attendance_notifier_8am',
  'attendance_notifier_10am',
  'attendance_notifier_3pm',
  'attendance_notifier_powerhouse',
  'biometric_retention',
] as const;

export type RegisteredJobName = (typeof REGISTERED_JOBS)[number];

export function fetchSystemStatus(): Promise<SystemStatus> {
  return api.get('/settings/system-status').then((r) => r.data);
}

export function fetchConfigChecklist(): Promise<ConfigChecklistResponse> {
  return api.get('/settings/config-checklist').then((r) => r.data);
}

export function fetchJobRuns(
  page: number,
  pageSize: number,
  jobName?: string,
): Promise<JobRunListResponse> {
  const params: Record<string, unknown> = { page, page_size: pageSize };
  if (jobName) params.job_name = jobName;
  return api.get('/settings/jobs', { params }).then((r) => r.data);
}

export function triggerJob(jobName: string): Promise<{ detail: string }> {
  return api.post(`/settings/jobs/${jobName}/trigger`).then((r) => r.data);
}

export function updateSetting(
  key: string,
  value: unknown,
): Promise<{ key: string; value: unknown }> {
  return api.put(`/settings/${key}`, { value }).then((r) => r.data);
}
