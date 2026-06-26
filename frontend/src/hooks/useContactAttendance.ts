import { useQuery } from '@tanstack/react-query';
import { contactsApi, type ContactAttendanceFilters } from '@/services/contacts';
import type { Paginated, ContactAttendanceItem } from '@/types';

export function useContactAttendance(
  id: number | undefined,
  filters: ContactAttendanceFilters = {},
) {
  return useQuery<Paginated<ContactAttendanceItem>>({
    queryKey: ['contact', id, 'attendance', filters],
    queryFn: () => contactsApi.getAttendance(id!, filters),
    enabled: id != null,
    staleTime: 30_000,
  });
}
