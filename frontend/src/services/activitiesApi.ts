import { api } from '@/services/api';
import type {
  ActivityDetail,
  ActivityCreate,
  ActivityUpdate,
  AssigneeOption,
  ActivityMeta,
  PaginatedActivities,
  ActivityFilters,
} from '@/types/activity';

export const activitiesApi = {
  list: (filters: ActivityFilters = {}): Promise<PaginatedActivities> =>
    api
      .get<PaginatedActivities>('/activities', { params: filters })
      .then((r) => r.data),

  mine: (filters: ActivityFilters = {}): Promise<PaginatedActivities> =>
    api
      .get<PaginatedActivities>('/activities/mine', { params: filters })
      .then((r) => r.data),

  get: (id: number): Promise<ActivityDetail> =>
    api.get<ActivityDetail>(`/activities/${id}`).then((r) => r.data),

  create: (body: ActivityCreate): Promise<ActivityDetail> =>
    api.post<ActivityDetail>('/activities', body).then((r) => r.data),

  update: (id: number, body: ActivityUpdate): Promise<ActivityDetail> =>
    api.patch<ActivityDetail>(`/activities/${id}`, body).then((r) => r.data),

  reassign: (id: number, assignee_user_id: number | null): Promise<ActivityDetail> =>
    api
      .post<ActivityDetail>(`/activities/${id}/reassign`, { assignee_user_id })
      .then((r) => r.data),

  remove: (id: number): Promise<void> =>
    api.delete(`/activities/${id}`).then(() => undefined),

  meta: (): Promise<ActivityMeta> =>
    api.get<ActivityMeta>('/activities/meta/types').then((r) => r.data),

  assignees: (): Promise<AssigneeOption[]> =>
    api.get<AssigneeOption[]>('/activities/assignees').then((r) => r.data),
};
