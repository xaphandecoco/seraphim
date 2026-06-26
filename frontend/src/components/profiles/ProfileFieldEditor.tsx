/**
 * ProfileFieldEditor — admin field list editor.
 *
 * Renders a sortable (up/down buttons) list of ProfileFieldDescriptor entries.
 * Per-field: label override, required toggle, section text input.
 * "Add field" dropdown lists available core fields + active custom-fields
 * from GET /custom-fields/schema?entity=contact (S02 endpoint).
 */

import { useState } from 'react';
import { Plus, Trash2, ChevronUp, ChevronDown } from 'lucide-react';
import { useCustomFieldSchema } from '@/hooks/useCustomFieldSchema';
import type { ProfileFieldDescriptor } from '@/types/profile';

// ---------- Constants --------------------------------------------------------

const CORE_FIELDS: { name: string; label: string }[] = [
  { name: 'first_name', label: 'First Name' },
  { name: 'last_name', label: 'Last Name' },
  { name: 'phone', label: 'Phone' },
  { name: 'email', label: 'Email' },
  { name: 'gender', label: 'Gender' },
  { name: 'birth_date', label: 'Birth Date' },
  { name: 'street_address', label: 'Street Address' },
  { name: 'contact_subtype', label: 'Contact Subtype' },
];

// ---------- Props ------------------------------------------------------------

interface ProfileFieldEditorProps {
  fields: ProfileFieldDescriptor[];
  onChange: (fields: ProfileFieldDescriptor[]) => void;
}

// ---------- Component --------------------------------------------------------

export function ProfileFieldEditor({ fields, onChange }: ProfileFieldEditorProps) {
  const [showPicker, setShowPicker] = useState(false);
  const { data: schemaData } = useCustomFieldSchema('contact');

  // ---- Mutation helpers ----------------------------------------------------

  const updateField = (idx: number, patch: Partial<ProfileFieldDescriptor>) => {
    const next = [...fields];
    next[idx] = { ...next[idx], ...patch };
    onChange(next);
  };

  const removeField = (idx: number) => {
    onChange(fields.filter((_, i) => i !== idx));
  };

  const moveField = (idx: number, direction: -1 | 1) => {
    const next = [...fields];
    const target = idx + direction;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target], next[idx]];
    // Re-assign weights by position
    onChange(next.map((f, i) => ({ ...f, weight: i * 10 })));
  };

  const addCoreField = (name: string) => {
    const id = `core:${name}`;
    if (fields.some((f) => f.id === id)) return; // deduplicate
    const next: ProfileFieldDescriptor = {
      id,
      field_type: 'core',
      core_field: name,
      label_override: null,
      placeholder: null,
      default_value: null,
      is_required: false,
      weight: fields.length * 10,
      section: null,
    };
    onChange([...fields, next]);
    setShowPicker(false);
  };

  const addCustomField = (name: string) => {
    const id = `custom:${name}`;
    if (fields.some((f) => f.id === id)) return;
    const next: ProfileFieldDescriptor = {
      id,
      field_type: 'custom',
      custom_field_name: name,
      label_override: null,
      placeholder: null,
      default_value: null,
      is_required: false,
      weight: fields.length * 10,
      section: null,
    };
    onChange([...fields, next]);
    setShowPicker(false);
  };

  // ---- Render --------------------------------------------------------------

  const alreadyUsedIds = new Set(fields.map((f) => f.id));

  return (
    <div className="space-y-3">
      {/* Field list */}
      {fields.length === 0 && (
        <p className="rounded-xl border border-dashed border-border py-6 text-center text-sm text-foreground/40">
          No fields yet. Add fields using the button below.
        </p>
      )}

      {fields.map((field, idx) => {
        const coreLabel = field.core_field
          ? (CORE_FIELDS.find((c) => c.name === field.core_field)?.label ?? field.core_field)
          : null;
        const baseLabel = coreLabel ?? field.custom_field_name ?? field.id;

        return (
          <div
            key={field.id}
            className="rounded-xl border border-border bg-card p-3 space-y-2"
          >
            {/* Row 1: field identity + reorder + remove */}
            <div className="flex items-center gap-2">
              <span className="flex-1 text-xs font-semibold text-foreground/70 truncate">
                {field.label_override || baseLabel}
                <span className="ml-1 text-foreground/30">({field.id})</span>
              </span>
              <button
                type="button"
                onClick={() => moveField(idx, -1)}
                disabled={idx === 0}
                aria-label="Move field up"
                className="rounded p-1 text-foreground/40 hover:bg-background hover:text-foreground disabled:opacity-30"
              >
                <ChevronUp size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => moveField(idx, 1)}
                disabled={idx === fields.length - 1}
                aria-label="Move field down"
                className="rounded p-1 text-foreground/40 hover:bg-background hover:text-foreground disabled:opacity-30"
              >
                <ChevronDown size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => removeField(idx)}
                aria-label={`Remove field ${baseLabel}`}
                className="rounded p-1 text-foreground/40 hover:bg-red-50 hover:text-red-500"
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>

            {/* Row 2: label override + section + required */}
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-0.5">
                <label
                  htmlFor={`label-${field.id}`}
                  className="text-[10px] font-semibold uppercase tracking-wide text-foreground/40"
                >
                  Label override
                </label>
                <input
                  id={`label-${field.id}`}
                  type="text"
                  value={field.label_override ?? ''}
                  placeholder={baseLabel}
                  onChange={(e) => updateField(idx, { label_override: e.target.value || null })}
                  className="rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div className="flex flex-col gap-0.5">
                <label
                  htmlFor={`section-${field.id}`}
                  className="text-[10px] font-semibold uppercase tracking-wide text-foreground/40"
                >
                  Section
                </label>
                <input
                  id={`section-${field.id}`}
                  type="text"
                  value={field.section ?? ''}
                  placeholder="General"
                  onChange={(e) => updateField(idx, { section: e.target.value || null })}
                  className="rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
            </div>

            {/* Row 3: required toggle */}
            <label className="flex items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                checked={field.is_required}
                onChange={(e) => updateField(idx, { is_required: e.target.checked })}
                className="rounded border-border accent-primary"
              />
              Required
            </label>
          </div>
        );
      })}

      {/* Add field button / picker */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setShowPicker((v) => !v)}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-primary/50 py-2.5 text-sm font-semibold text-primary hover:bg-primary/5"
        >
          <Plus size={15} aria-hidden="true" />
          Add field
        </button>

        {showPicker && (
          <div className="absolute bottom-full left-0 right-0 z-30 mb-1 max-h-64 overflow-y-auto rounded-xl border border-border bg-card shadow-xl">
            {/* Core fields */}
            <div className="px-3 py-2 text-[10px] font-bold uppercase tracking-wide text-foreground/40">
              Core fields
            </div>
            {CORE_FIELDS.map((cf) => {
              const id = `core:${cf.name}`;
              const used = alreadyUsedIds.has(id);
              return (
                <button
                  key={cf.name}
                  type="button"
                  disabled={used}
                  onClick={() => addCoreField(cf.name)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground hover:bg-background disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {cf.label}
                  {used && <span className="ml-auto text-[10px] text-foreground/40">added</span>}
                </button>
              );
            })}

            {/* Custom fields from S02 schema */}
            {schemaData?.groups && schemaData.groups.length > 0 && (
              <>
                <div className="border-t border-border px-3 py-2 text-[10px] font-bold uppercase tracking-wide text-foreground/40">
                  Custom fields
                </div>
                {schemaData.groups.map((group) =>
                  group.fields
                    .filter((f) => f.is_active)
                    .map((f) => {
                      const id = `custom:${f.name}`;
                      const used = alreadyUsedIds.has(id);
                      return (
                        <button
                          key={f.name}
                          type="button"
                          disabled={used}
                          onClick={() => addCustomField(f.name)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground hover:bg-background disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <span>{f.label}</span>
                          <span className="ml-1 text-foreground/30 text-xs">({group.label})</span>
                          {used && (
                            <span className="ml-auto text-[10px] text-foreground/40">added</span>
                          )}
                        </button>
                      );
                    }),
                )}
              </>
            )}

            <button
              type="button"
              onClick={() => setShowPicker(false)}
              className="w-full border-t border-border py-2 text-xs text-foreground/40 hover:bg-background"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
