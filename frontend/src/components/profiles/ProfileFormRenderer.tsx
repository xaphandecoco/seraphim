/**
 * ProfileFormRenderer — schema-driven form renderer.
 *
 * Accepts a resolved profile schema (from /profiles/:id/render or
 * /public/newcomer/profile) and renders the corresponding form fields.
 * Groups fields by `section`, sorts by `weight` within each group.
 * Handles validation, honeypot (isPublic), and submit state.
 */

import { useState, useEffect } from 'react';
import { SectionRenderer } from './SectionRenderer';
import { getFieldKey } from './fieldUtils';
import type { ProfileRenderResponse, PublicProfileSchema, ResolvedField } from '@/types/profile';

// ---------- Props -------------------------------------------------------------

interface ProfileFormRendererProps {
  schema: ProfileRenderResponse | PublicProfileSchema;
  prefill?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
  submitLabel?: string;
  isPublic?: boolean;
}

// ---------- Helpers -----------------------------------------------------------

/** Group fields by section, preserving minimum weight order within each group. */
function groupBySection(
  fields: ResolvedField[],
): { title: string; fields: ResolvedField[] }[] {
  const map = new Map<string, ResolvedField[]>();

  const sorted = [...fields].sort((a, b) => a.weight - b.weight);
  for (const f of sorted) {
    const sec = f.section ?? 'General';
    if (!map.has(sec)) map.set(sec, []);
    map.get(sec)!.push(f);
  }

  return Array.from(map.entries()).map(([title, sectionFields]) => ({
    title,
    fields: sectionFields,
  }));
}

/** Build initial form values from field defaults and optional prefill. */
function buildInitialValues(
  fields: ResolvedField[],
  prefill?: Record<string, unknown>,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const f of fields) {
    const key = getFieldKey(f);
    values[key] = prefill?.[key] ?? f.default_value ?? '';
  }
  return values;
}

// ---------- Component ---------------------------------------------------------

export function ProfileFormRenderer({
  schema,
  prefill,
  onSubmit,
  submitLabel,
  isPublic = false,
}: ProfileFormRendererProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    buildInitialValues(schema.fields, prefill),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [honeypot, setHoneypot] = useState('');

  // Re-initialise values if schema changes (e.g., live preview rebuild)
  useEffect(() => {
    setValues(buildInitialValues(schema.fields, prefill));
    setErrors({});
  }, [schema, prefill]);

  const sections = groupBySection(schema.fields);

  // ---- Per-field handlers ---------------------------------------------------

  const handleChange = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    // Clear inline error once the user starts typing
    if (errors[key]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  const handleBlur = (key: string, field: ResolvedField) => {
    if (field.is_required && !values[key]) {
      setErrors((prev) => ({ ...prev, [key]: `${field.label} is required` }));
    }
  };

  // ---- Submit ---------------------------------------------------------------

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate all required fields
    const newErrors: Record<string, string> = {};
    for (const f of schema.fields) {
      const key = getFieldKey(f);
      const val = values[key];
      if (f.is_required && (!val || (typeof val === 'string' && val.trim() === ''))) {
        newErrors[key] = `${f.label} is required`;
      }
    }

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      return;
    }

    const payload: Record<string, unknown> = { ...values };
    if (isPublic) {
      payload.website = honeypot;
    }

    setIsSubmitting(true);
    try {
      await onSubmit(payload);
    } finally {
      setIsSubmitting(false);
    }
  };

  // ---- Render ---------------------------------------------------------------

  const label =
    submitLabel ?? schema.settings.submit_label ?? 'Submit';

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Profile form">
      {/* ---- Honeypot (public only) — visually hidden from real users ---- */}
      {isPublic && (
        <div className="hidden" aria-hidden="true">
          <input
            type="text"
            name="website"
            value={honeypot}
            onChange={(e) => setHoneypot(e.target.value)}
            tabIndex={-1}
            autoComplete="off"
          />
        </div>
      )}

      <div className="space-y-6">
        {sections.map((sec) => (
          <SectionRenderer
            key={sec.title}
            title={sec.title}
            fields={sec.fields}
            values={values}
            errors={errors}
            onChange={handleChange}
            onBlur={handleBlur}
          />
        ))}
      </div>

      <div className="mt-6">
        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-60 disabled:cursor-not-allowed"
          aria-busy={isSubmitting}
        >
          {isSubmitting && (
            <span
              className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent"
              aria-hidden="true"
            />
          )}
          {label}
        </button>
      </div>
    </form>
  );
}
