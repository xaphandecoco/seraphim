import { useQuery } from '@tanstack/react-query';
import { contactsApi } from '@/services/contacts';
import type { ContactDetail } from '@/types';

export function useContact(id: number | undefined) {
  return useQuery<ContactDetail>({
    queryKey: ['contact', id],
    queryFn: () => contactsApi.get(id!),
    enabled: id != null,
    staleTime: 30_000,
  });
}
