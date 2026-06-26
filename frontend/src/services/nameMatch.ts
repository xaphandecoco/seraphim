import { api } from '@/services/api';
import type {
  PaginatedReviewQueueResponse,
  ReviewQueueItem,
  ReviewQueueResolveRequest,
} from '@/types/nameMatch';

export interface ReviewQueueFilters {
  status?: string;
  source?: string;
  event_id?: number;
  page?: number;
  page_size?: number;
}

export async function getReviewQueue(filters: ReviewQueueFilters = {}): Promise<PaginatedReviewQueueResponse> {
  const resp = await api.get<PaginatedReviewQueueResponse>('/name-match/review-queue', { params: filters });
  return resp.data;
}

export async function resolveQueueItem(id: number, payload: ReviewQueueResolveRequest): Promise<ReviewQueueItem> {
  const resp = await api.post<ReviewQueueItem>(`/name-match/review-queue/${id}/resolve`, payload);
  return resp.data;
}

export async function unmatchQueueItem(id: number, reason?: string): Promise<ReviewQueueItem> {
  const resp = await api.post<ReviewQueueItem>(`/name-match/review-queue/${id}/unmatch`, { reason });
  return resp.data;
}

export async function skipQueueItem(id: number): Promise<ReviewQueueItem> {
  const resp = await api.post<ReviewQueueItem>(`/name-match/review-queue/${id}/skip`, {});
  return resp.data;
}

export async function reprocessAliases(): Promise<{ processed: number; auto_matched: number; still_pending: number }> {
  const resp = await api.post('/name-match/review-queue/reprocess-aliases', {});
  return resp.data;
}

export async function getPendingCount(): Promise<number> {
  const resp = await api.get<PaginatedReviewQueueResponse>('/name-match/review-queue', {
    params: { status: 'pending', page_size: 1 },
  });
  return resp.data.total;
}
