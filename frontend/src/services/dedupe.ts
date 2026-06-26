// S11 — Find & Merge Duplicates — service layer.
// All calls go through the shared `api` axios client (no /api prefix — nginx strips it).

import { api } from '@/services/api';
import type {
  CandidatePairList,
  CandidateRunRequest,
  MergePreviewResponse,
  MergeExecuteRequest,
  MergeExecuteResponse,
  MergeHistoryList,
  RuleSet,
  RuleSetCreate,
  RuleSetUpdate,
} from '@/types/dedupe';

// ─── Rule sets ────────────────────────────────────────────────────────────────

export async function listRuleSets(): Promise<RuleSet[]> {
  const resp = await api.get<RuleSet[]>('/dedupe/rule-sets');
  return resp.data;
}

export async function createRuleSet(data: RuleSetCreate): Promise<RuleSet> {
  const resp = await api.post<RuleSet>('/dedupe/rule-sets', data);
  return resp.data;
}

export async function updateRuleSet(id: number, data: RuleSetUpdate): Promise<RuleSet> {
  const resp = await api.put<RuleSet>(`/dedupe/rule-sets/${id}`, data);
  return resp.data;
}

export async function deleteRuleSet(id: number): Promise<void> {
  await api.delete(`/dedupe/rule-sets/${id}`);
}

// ─── Candidates ───────────────────────────────────────────────────────────────

export async function findCandidates(req: CandidateRunRequest): Promise<CandidatePairList> {
  const resp = await api.post<CandidatePairList>('/dedupe/candidates', req);
  return resp.data;
}

// ─── Merge preview ────────────────────────────────────────────────────────────

export async function mergePreview(
  survivor_id: number,
  loser_id: number,
): Promise<MergePreviewResponse> {
  const resp = await api.post<MergePreviewResponse>('/dedupe/merge/preview', {
    survivor_id,
    loser_id,
  });
  return resp.data;
}

// ─── Merge execute ────────────────────────────────────────────────────────────

export async function mergeContacts(req: MergeExecuteRequest): Promise<MergeExecuteResponse> {
  const resp = await api.post<MergeExecuteResponse>('/dedupe/merge', req);
  return resp.data;
}

// ─── Merge history ────────────────────────────────────────────────────────────

export async function getMergeHistory(
  page: number,
  page_size: number,
): Promise<MergeHistoryList> {
  const resp = await api.get<MergeHistoryList>('/dedupe/merge/history', {
    params: { page, page_size },
  });
  return resp.data;
}
