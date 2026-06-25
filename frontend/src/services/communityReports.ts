import { api } from '@/services/api';
import type {
  CommunityReport,
  CommunityReportDetail,
  CommunityReportCreate,
  PaginatedCommunityReportResponse,
} from '@/types/nameMatch';

export interface CommunityReportFilters {
  status?: string;
  zone?: string;
  event_id?: number;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export async function listCommunityReports(filters: CommunityReportFilters = {}): Promise<PaginatedCommunityReportResponse> {
  const resp = await api.get<PaginatedCommunityReportResponse>('/community-reports', { params: filters });
  return resp.data;
}

export async function getCommunityReport(id: number): Promise<CommunityReportDetail> {
  const resp = await api.get<CommunityReportDetail>(`/community-reports/${id}`);
  return resp.data;
}

export async function createCommunityReport(payload: CommunityReportCreate): Promise<CommunityReport> {
  const resp = await api.post<CommunityReport>('/community-reports', payload);
  return resp.data;
}

export async function processCommunityReport(id: number): Promise<{ matched: number; review_queue: number }> {
  const resp = await api.post(`/community-reports/${id}/process`, {});
  return resp.data;
}

export async function archiveCommunityReport(id: number): Promise<void> {
  await api.delete(`/community-reports/${id}`);
}
