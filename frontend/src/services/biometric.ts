// S08 — Biometric Consent & RTBF — API service layer
import { api } from '@/services/api';
import type {
  ConsentResponse,
  PurgeResultResponse,
  RetentionReportResponse,
  ConsentRecordRequest,
  ConsentUpdateRequest,
  DeletionRequest,
  RetentionReportParams,
} from '@/types/biometric';

export const biometricApi = {
  /** GET /biometric/contacts/{id}/consent — never 404; status='none' when no row */
  getConsent: (contactId: number): Promise<ConsentResponse> =>
    api
      .get<ConsentResponse>(`/biometric/contacts/${contactId}/consent`)
      .then((r) => r.data),

  /** POST /biometric/contacts/{id}/consent — record new consent (volunteer+). 409 if already purged. */
  recordConsent: (
    contactId: number,
    body: ConsentRecordRequest,
  ): Promise<ConsentResponse> =>
    api
      .post<ConsentResponse>(`/biometric/contacts/${contactId}/consent`, body)
      .then((r) => r.data),

  /** PATCH /biometric/contacts/{id}/consent — update note/retention_until. 404 if none.
   *  retention_until is admin-only (backend returns 403 for volunteer). */
  updateConsent: (
    contactId: number,
    body: ConsentUpdateRequest,
  ): Promise<ConsentResponse> =>
    api
      .patch<ConsentResponse>(`/biometric/contacts/${contactId}/consent`, body)
      .then((r) => r.data),

  /** POST /biometric/contacts/{id}/consent/revoke — volunteer+ */
  revokeConsent: (contactId: number): Promise<ConsentResponse> =>
    api
      .post<ConsentResponse>(`/biometric/contacts/${contactId}/consent/revoke`)
      .then((r) => r.data),

  /** POST /biometric/contacts/{id}/deletion-request — volunteer+
   *  Returns PurgeResultResponse if immediate=true and caller is admin */
  requestDeletion: (
    contactId: number,
    body: DeletionRequest,
  ): Promise<ConsentResponse | PurgeResultResponse> =>
    api
      .post(`/biometric/contacts/${contactId}/deletion-request`, body)
      .then((r) => r.data),

  /** POST /biometric/contacts/{id}/purge — admin only */
  purge: (contactId: number): Promise<PurgeResultResponse> =>
    api
      .post<PurgeResultResponse>(`/biometric/contacts/${contactId}/purge`)
      .then((r) => r.data),

  /** GET /biometric/retention/report — admin only, paginated */
  getRetentionReport: (params: RetentionReportParams): Promise<RetentionReportResponse> =>
    api
      .get<RetentionReportResponse>('/biometric/retention/report', { params })
      .then((r) => r.data),
};
