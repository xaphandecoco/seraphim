import type { ResolvedField } from '@/types/profile';

/**
 * Derive the storage key from a ResolvedField.
 * Core fields use the core_field name; custom fields use custom_field_name.
 * Both are used as keys in the form values map.
 */
export function getFieldKey(field: ResolvedField): string {
  if (field.field_type === 'core') return field.core_field ?? field.id;
  return field.custom_field_name ?? field.id.replace(/^custom:/, '');
}
