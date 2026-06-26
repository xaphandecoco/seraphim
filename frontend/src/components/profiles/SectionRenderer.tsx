import { FormField, inputClass } from '@/components/ui/FormField';
import type { ResolvedField } from '@/types/profile';
import type { OptionItem } from '@/types/customFields';
import { getFieldKey } from './fieldUtils';

// ---------- helpers -----------------------------------------------------------

/** Map a ResolvedField to the input type / variant we should render. */
function resolveInputKind(field: ResolvedField): string {
  if (field.field_type === 'core') {
    if (field.core_field === 'gender') return 'select_gender';
    if (field.core_field === 'birth_date') return 'date';
    return 'text';
  }
  return field.data_type ?? 'text';
}

/** Normalize the options array to `{ value, label }[]` regardless of backend shape. */
function normalizeOptions(options?: OptionItem[] | string[]): { value: string; label: string }[] {
  if (!options) return [];
  return options.map((o) =>
    typeof o === 'string' ? { value: o, label: o } : (o as { value: string; label: string }),
  );
}

// ---------- Props ------------------------------------------------------------

interface SectionRendererProps {
  title: string | null;
  fields: ResolvedField[];
  values: Record<string, unknown>;
  errors: Record<string, string>;
  onChange: (key: string, value: unknown) => void;
  onBlur: (key: string, field: ResolvedField) => void;
}

// ---------- Component --------------------------------------------------------

export function SectionRenderer({
  title,
  fields,
  values,
  errors,
  onChange,
  onBlur,
}: SectionRendererProps) {
  return (
    <div className="space-y-4">
      {title && title !== 'General' && (
        <h3 className="text-sm font-bold uppercase tracking-wide text-foreground/50">
          {title}
        </h3>
      )}

      {fields.map((field) => {
        const key = getFieldKey(field);
        const kind = resolveInputKind(field);
        const value = values[key] ?? field.default_value ?? '';
        const error = errors[key];
        const opts = normalizeOptions(field.options as OptionItem[] | string[] | undefined);

        return (
          <FormField
            key={field.id}
            label={field.label}
            htmlFor={`field-${field.id}`}
            required={field.is_required}
            error={error}
          >
            {/* ---- select_gender ---- */}
            {kind === 'select_gender' && (
              <select
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              >
                <option value="">Select…</option>
                <option value="Male">Male</option>
                <option value="Female">Female</option>
                <option value="Other">Other</option>
              </select>
            )}

            {/* ---- select (from options) ---- */}
            {kind === 'select' && (
              <select
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              >
                <option value="">Select…</option>
                {opts.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            )}

            {/* ---- multiselect (checkbox group) ---- */}
            {kind === 'multiselect' && (
              <div className="flex flex-wrap gap-3 pt-1">
                {opts.map((o) => {
                  const checked = Array.isArray(value) && (value as string[]).includes(o.value);
                  return (
                    <label
                      key={o.value}
                      className="flex cursor-pointer items-center gap-1.5 text-sm text-foreground"
                    >
                      <input
                        type="checkbox"
                        name={key}
                        value={o.value}
                        checked={checked}
                        onChange={(e) => {
                          const prev = Array.isArray(value) ? (value as string[]) : [];
                          const next = e.target.checked
                            ? [...prev, o.value]
                            : prev.filter((v) => v !== o.value);
                          onChange(key, next);
                        }}
                        className="rounded border-border accent-primary"
                      />
                      {o.label}
                    </label>
                  );
                })}
              </div>
            )}

            {/* ---- date ---- */}
            {kind === 'date' && (
              <input
                type="date"
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                placeholder={field.placeholder ?? undefined}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              />
            )}

            {/* ---- textarea ---- */}
            {kind === 'textarea' && (
              <textarea
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                placeholder={field.placeholder ?? undefined}
                rows={3}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              />
            )}

            {/* ---- checkbox ---- */}
            {kind === 'checkbox' && (
              <input
                type="checkbox"
                id={`field-${field.id}`}
                name={key}
                checked={Boolean(value)}
                onChange={(e) => onChange(key, e.target.checked)}
                onBlur={() => onBlur(key, field)}
                className="h-4 w-4 rounded border-border accent-primary"
              />
            )}

            {/* ---- number ---- */}
            {kind === 'number' && (
              <input
                type="number"
                id={`field-${field.id}`}
                name={key}
                value={value === '' ? '' : String(value)}
                placeholder={field.placeholder ?? undefined}
                onChange={(e) => onChange(key, e.target.value === '' ? '' : Number(e.target.value))}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              />
            )}

            {/* ---- contact_reference → free-text (server resolves) ---- */}
            {kind === 'contact_reference' && (
              <input
                type="text"
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                placeholder={field.placeholder ?? 'Type a name…'}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              />
            )}

            {/* ---- text / email / phone / default ---- */}
            {(kind === 'text' || (kind !== 'select_gender' &&
              kind !== 'select' &&
              kind !== 'multiselect' &&
              kind !== 'date' &&
              kind !== 'textarea' &&
              kind !== 'checkbox' &&
              kind !== 'number' &&
              kind !== 'contact_reference')) && (
              <input
                type="text"
                id={`field-${field.id}`}
                name={key}
                value={String(value)}
                placeholder={field.placeholder ?? undefined}
                onChange={(e) => onChange(key, e.target.value)}
                onBlur={() => onBlur(key, field)}
                className={inputClass}
              />
            )}
          </FormField>
        );
      })}
    </div>
  );
}
