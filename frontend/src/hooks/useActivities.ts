import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { activitiesApi } from '@/services/activitiesApi';
import type {
  ActivityFilters,
  ActivityCreate,
  ActivityUpdate,
  ActivityDetail,
  ActivityStatus,
} from '@/types/activity';

// ---------------------------------------------------------------------------
// Query keys — all prefixed ['activities',...] to avoid collision with the
// existing face-detection ['tasks',...] keys.
// ---------------------------------------------------------------------------
const activityKeys = {
  list: (f: ActivityFilters) => ['activities', 'list', f] as const,
  mine: (f: ActivityFilters) => ['activities', 'mine', f] as const,
  detail: (id: number) => ['activities', 'detail', id] as const,
  byContact: (contactId: number) => ['activities', 'byContact', contactId] as const,
  meta: () => ['activities', 'meta'] as const,
  assignees: () => ['activities', 'assignees'] as const,
  overdueBadge: () => ['activities', 'overdueBadge'] as const,
};

// ---------------------------------------------------------------------------
// Read hooks
// ---------------------------------------------------------------------------

export function useActivities(filters: ActivityFilters = {}) {
  return useQuery({
    queryKey: activityKeys.list(filters),
    queryFn: () => activitiesApi.list(filters),
  });
}

export function useMyActivities(filters: ActivityFilters = {}) {
  return useQuery({
    queryKey: activityKeys.mine(filters),
    queryFn: () => activitiesApi.mine(filters),
  });
}

export function useActivity(id: number | undefined) {
  return useQuery({
    queryKey: activityKeys.detail(id!),
    queryFn: () => activitiesApi.get(id!),
    enabled: id != null,
  });
}

export function useActivitiesByContact(contactId: number | undefined) {
  return useQuery({
    queryKey: activityKeys.byContact(contactId!),
    queryFn: () => activitiesApi.list({ target_contact_id: contactId }),
    enabled: contactId != null,
  });
}

export function useActivityMeta() {
  return useQuery({
    queryKey: activityKeys.meta(),
    queryFn: () => activitiesApi.meta(),
    staleTime: 10 * 60 * 1_000, // 10 minutes — enum types rarely change
  });
}

export function useAssignees() {
  return useQuery({
    queryKey: activityKeys.assignees(),
    queryFn: () => activitiesApi.assignees(),
    staleTime: 5 * 60 * 1_000, // 5 minutes
  });
}

/**
 * Polls the caller's overdue-activity count for the BottomNav badge.
 * Caller must pass `enabled: !!isAuthenticated` to suppress the query while
 * the user is unauthenticated (avoids a 401 on page load before the refresh
 * cycle completes).
 */
export function useOverdueBadgeCount({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: activityKeys.overdueBadge(),
    queryFn: async () => {
      const data = await activitiesApi.mine({ overdue: true, page_size: 1 });
      return data.total;
    },
    refetchInterval: 60_000,
    enabled,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useCreateActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ActivityCreate) => activitiesApi.create(body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['activities', 'list'] });
      qc.invalidateQueries({ queryKey: ['activities', 'mine'] });
      qc.invalidateQueries({ queryKey: ['activities', 'overdueBadge'] });
      if (data.target_contact_id != null) {
        qc.invalidateQueries({
          queryKey: ['activities', 'byContact', data.target_contact_id],
        });
      }
    },
  });
}

export function useUpdateActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ActivityUpdate }) =>
      activitiesApi.update(id, body),
    onSuccess: (data, variables) => {
      qc.invalidateQueries({ queryKey: ['activities', 'list'] });
      qc.invalidateQueries({ queryKey: ['activities', 'mine'] });
      qc.invalidateQueries({ queryKey: ['activities', 'detail', variables.id] });
      qc.invalidateQueries({ queryKey: ['activities', 'overdueBadge'] });
      if (data.target_contact_id != null) {
        qc.invalidateQueries({
          queryKey: ['activities', 'byContact', data.target_contact_id],
        });
      }
    },
  });
}

export function useReassignActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      assignee_user_id,
    }: {
      id: number;
      assignee_user_id: number | null;
    }) => activitiesApi.reassign(id, assignee_user_id),
    onSuccess: (data, variables) => {
      qc.invalidateQueries({ queryKey: ['activities', 'list'] });
      qc.invalidateQueries({ queryKey: ['activities', 'mine'] });
      qc.invalidateQueries({ queryKey: ['activities', 'detail', variables.id] });
      qc.invalidateQueries({ queryKey: ['activities', 'overdueBadge'] });
      if (data.target_contact_id != null) {
        qc.invalidateQueries({
          queryKey: ['activities', 'byContact', data.target_contact_id],
        });
      }
    },
  });
}

export function useDeleteActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
    }: {
      id: number;
      targetContactId?: number | null;
    }) => activitiesApi.remove(id),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['activities', 'list'] });
      qc.invalidateQueries({ queryKey: ['activities', 'mine'] });
      qc.invalidateQueries({ queryKey: ['activities', 'overdueBadge'] });
      if (variables.targetContactId != null) {
        qc.invalidateQueries({
          queryKey: ['activities', 'byContact', variables.targetContactId],
        });
      }
    },
  });
}

// Re-export type so pages/components can import it without reaching into react-query
export type { ActivityStatus };
export type { ActivityDetail };
