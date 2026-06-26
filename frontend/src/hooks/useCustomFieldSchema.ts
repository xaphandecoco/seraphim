import { useQuery } from '@tanstack/react-query';
import { customFieldsApi } from '@/services/customFields';

export function useCustomFieldSchema(entity = 'contact') {
  return useQuery({
    queryKey: ['custom-fields', 'schema', entity],
    queryFn: () => customFieldsApi.getSchema(entity),
    staleTime: 5 * 60 * 1000, // 5 min
  });
}
