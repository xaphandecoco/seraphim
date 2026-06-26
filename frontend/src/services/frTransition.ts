import { api } from '@/services/api';
import type {
  FRTransitionStatus,
  RemapReport,
  ConsentBackfillReport,
  OrphanSubjectPage,
} from '@/types';

export async function getFRTransitionStatus(): Promise<FRTransitionStatus> {
  const resp = await api.get<FRTransitionStatus>('/admin/fr-transition/status');
  return resp.data;
}

export async function runRemap(): Promise<RemapReport> {
  const resp = await api.post<RemapReport>('/admin/fr-transition/remap');
  return resp.data;
}

export async function runConsentBackfill(): Promise<ConsentBackfillReport> {
  const resp = await api.post<ConsentBackfillReport>('/admin/fr-transition/consent-backfill');
  return resp.data;
}

export async function getOrphans(page = 1, pageSize = 50): Promise<OrphanSubjectPage> {
  const resp = await api.get<OrphanSubjectPage>('/admin/fr-transition/orphans', {
    params: { page, page_size: pageSize },
  });
  return resp.data;
}

export async function relinkOrphan(subjectId: number, contactId: number): Promise<void> {
  await api.patch(`/admin/fr-transition/orphans/${subjectId}/relink`, { contact_id: contactId });
}

export async function retireOrphan(subjectId: number): Promise<void> {
  await api.patch(`/admin/fr-transition/orphans/${subjectId}/retire`);
}
