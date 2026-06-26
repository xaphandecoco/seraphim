import { api } from '@/services/api';
import type { NameListIntakeResponse } from '@/types/nameMatch';

export async function submitNameList(payload: {
  event_id: number;
  names: string[];
  source?: string;
  community_report_id?: number | null;
}): Promise<NameListIntakeResponse> {
  const resp = await api.post<NameListIntakeResponse>('/attendance/name-list', payload);
  return resp.data;
}
