import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  contactsApi,
  type ContactFilters,
  type ContactCreate,
  type ContactUpdate,
  type ContactAttendanceFilters,
} from '@/services/contacts';
import type { Paginated, ContactListItem, ContactDetail, ContactAttendanceItem } from '@/types';

// ---------- Query hooks -------------------------------------------------------

export function useContactList(params: ContactFilters = {}) {
  return useQuery<Paginated<ContactListItem>>({
    queryKey: ['contacts', params],
    queryFn: () => contactsApi.list(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useContact(id: number | undefined) {
  return useQuery<ContactDetail>({
    queryKey: ['contact', id],
    queryFn: () => contactsApi.get(id!),
    enabled: id != null,
    staleTime: 30_000,
  });
}

export function useContactAttendance(
  id: number | undefined,
  params: ContactAttendanceFilters = {},
) {
  return useQuery<Paginated<ContactAttendanceItem>>({
    queryKey: ['contact', id, 'attendance', params],
    queryFn: () => contactsApi.getAttendance(id!, params),
    enabled: id != null,
    staleTime: 30_000,
  });
}

// ---------- Mutation hooks ----------------------------------------------------

export function useCreateContact() {
  const queryClient = useQueryClient();
  return useMutation<ContactDetail, Error, ContactCreate>({
    mutationFn: (payload) => contactsApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
    },
  });
}

export function useUpdateContact(id: number) {
  const queryClient = useQueryClient();
  return useMutation<ContactDetail, Error, ContactUpdate>({
    mutationFn: (payload) => contactsApi.update(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
    },
  });
}

export function useDeleteContact(id: number) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: (cid) => contactsApi.delete(cid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
    },
  });
}

export function useRestoreContact(id: number) {
  const queryClient = useQueryClient();
  return useMutation<ContactDetail, Error, number>({
    mutationFn: (cid) => contactsApi.restore(cid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
    },
  });
}

// ---------- Legacy alias (used by ContactsPage) -------------------------------

/** @deprecated use useContactList */
export function useContacts(filters: ContactFilters = {}) {
  return useContactList(filters);
}
