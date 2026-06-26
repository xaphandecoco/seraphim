import { CustomFieldRenderer } from './CustomFieldRenderer';
import type { CustomFieldGroup, CustomDataValue } from '@/types/customFields';

interface CustomFieldsSectionProps {
  group: CustomFieldGroup;
  value: Record<string, CustomDataValue>;
  onChange: (name: string, v: CustomDataValue) => void;
  disabled?: boolean;
  errors?: Record<string, string>;
}

export function CustomFieldsSection({
  group,
  value,
  onChange,
  disabled = false,
  errors = {},
}: CustomFieldsSectionProps) {
  // Only render active fields, in weight order (backend should already sort, but
  // we sort client-side too so the component is self-contained).
  const activeFields = [...group.fields]
    .filter((f) => f.is_active)
    .sort((a, b) => a.weight - b.weight);

  if (activeFields.length === 0) return null;

  return (
    <section aria-labelledby={`cfg-section-${group.id}`} className="space-y-4">
      <h3
        id={`cfg-section-${group.id}`}
        className="text-sm font-semibold text-foreground"
      >
        {group.label}
      </h3>

      <div className="space-y-4">
        {activeFields.map((field) => (
          <CustomFieldRenderer
            key={field.id}
            field={field}
            value={value[field.name] ?? null}
            onChange={(v) => onChange(field.name, v)}
            disabled={disabled}
            error={errors[field.name]}
          />
        ))}
      </div>
    </section>
  );
}
