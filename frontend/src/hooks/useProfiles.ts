import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { profilesApi, publicNewcomerApi } from '@/services/profilesApi';
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

// ---------- Query hooks -------------------------------------------------------

/** List profiles with pagination and filters. Pass `{ enabled: false }` to disable fetch. */
export function useProfiles(
  filters: ProfileFilters = {},
  options: { enabled?: boolean } = {},
) {
  return useQuery<PaginatedProfileResponse>({
    queryKey: ['profiles', filters],
    queryFn: () => profilesApi.list(filters),
    staleTime: 30_000,
    enabled: options.enabled ?? true,
  });
}

/** Fetch a single profile by id. */
export function useProfile(id: number | undefined) {
  return useQuery<ProfileResponse>({
    queryKey: ['profiles', id],
    queryFn: () => profilesApi.get(id!),
    enabled: id != null,
    staleTime: 30_000,
  });
}

/** Render a profile schema for use in the internal form launcher. */
export function useRenderProfile(
  id: number | undefined,
  prefill: Record<string, unknown> = {},
) {
  return useQuery<ProfileRenderResponse>({
    queryKey: ['profiles', id, 'render', prefill],
    queryFn: () => profilesApi.render(id!, prefill),
    enabled: id != null,
    staleTime: 30_000,
  });
}

/** Fetch the public newcomer form schema (unauthenticated). */
export function usePublicNewcomerProfile() {
  return useQuery<PublicProfileSchema>({
    queryKey: ['public-newcomer-profile'],
    queryFn: () => publicNewcomerApi.getProfile(),
    staleTime: 5 * 60 * 1_000,
    retry: 1,
  });
}

// ---------- Mutation hooks ----------------------------------------------------

export function useCreateProfile() {
  const queryClient = useQueryClient();
  return useMutation<ProfileResponse, Error, ProfileCreate>({
    mutationFn: (body) => profilesApi.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
  });
}

export function useUpdateProfile(id: number) {
  const queryClient = useQueryClient();
  return useMutation<ProfileResponse, Error, ProfileUpdate>({
    mutationFn: (body) => profilesApi.update(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
  });
}

export function useDeleteProfile() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: (profileId) => profilesApi.delete(profileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
  });
}

export function useSubmitNewcomer() {
  return useMutation<NewcomerResult, Error, NewcomerSubmission>({
    mutationFn: (payload) => publicNewcomerApi.submit(payload),
  });
}
