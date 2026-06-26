import { api } from '@/services/api';
import type {
  ProfileFilters,
  ProfileCreate,
  ProfileUpdate,
  ProfileResponse,
  PaginatedProfileResponse,
  ProfileRenderResponse,
  PublicProfileSchema,
  NewcomerSubmission,
  NewcomerResult,
} from '@/types/profile';

// ---------- Admin CRUD --------------------------------------------------------

export const profilesApi = {
  list: (filters: ProfileFilters = {}): Promise<PaginatedProfileResponse> =>
    api
      .get<PaginatedProfileResponse>('/profiles', { params: filters })
      .then((r) => r.data),

  get: (id: number): Promise<ProfileResponse> =>
    api.get<ProfileResponse>(`/profiles/${id}`).then((r) => r.data),

  create: (body: ProfileCreate): Promise<ProfileResponse> =>
    api.post<ProfileResponse>('/profiles', body).then((r) => r.data),

  update: (id: number, body: ProfileUpdate): Promise<ProfileResponse> =>
    api.put<ProfileResponse>(`/profiles/${id}`, body).then((r) => r.data),

  delete: (id: number): Promise<void> =>
    api.delete(`/profiles/${id}`).then(() => undefined),

  render: (
    id: number,
    prefill: Record<string, unknown> = {},
  ): Promise<ProfileRenderResponse> =>
    api
      .post<ProfileRenderResponse>(`/profiles/${id}/render`, { prefill })
      .then((r) => r.data),
};

// ---------- Public newcomer (no auth required) --------------------------------

export const publicNewcomerApi = {
  getProfile: (): Promise<PublicProfileSchema> =>
    api.get<PublicProfileSchema>('/public/newcomer/profile').then((r) => r.data),

  submit: (payload: NewcomerSubmission): Promise<NewcomerResult> =>
    api.post<NewcomerResult>('/public/newcomer', payload).then((r) => r.data),
};
