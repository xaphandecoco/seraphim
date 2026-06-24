import { useState } from 'react';
import { X } from 'lucide-react';
import { MemberSearchModal } from '@/components/tasks/MemberSearchModal';
import type { Member } from '@/types';
import type { CustomFieldDef, CustomDataValue, OptionItem } from '@/types/customFields';

interface CustomFieldRendererProps {
  field: CustomFieldDef;
  value: CustomDataValue;
  onChange: (v: CustomDataValue) => void;
  disabled?: boolean;
  error?: string;
}

const baseInputClass =
  'w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground ' +
  'outline-none placeholder:text-foreground/50 focus:border-primary focus:ring-2 focus:ring-primary/30 ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';

export function CustomFieldRenderer({
  field,
  value,
  onChange,
  disabled = false,
  error,
}: CustomFieldRendererProps) {
  const [pickerOpen, setPickerOpen] = useState(false);

  const labelId = `cf-label-${field.id}`;
  const inputId = `cf-input-${field.id}`;
  const helpId = field.help_text ? `cf-help-${field.id}` : undefined;
  const errorId = error ? `cf-error-${field.id}` : undefined;

  const describedBy =
    [helpId, errorId].filter((x): x is string => x !== undefined).join(' ') || undefined;

  // ---- helpers -----------------------------------------------------------

  function asStringArray(v: CustomDataValue): string[] {
    if (Array.isArray(v)) return v.map(String);
    return [];
  }

  function asNumberArray(v: CustomDataValue): number[] {
    if (Array.isArray(v)) return (v as unknown[]).filter((x): x is number => typeof x === 'number');
    return [];
  }

  // Chip for multiselect / contact_reference
  function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
        {label}
        {!disabled && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${label}`}
            className="ml-0.5 flex h-4 w-4 items-center justify-center rounded-full hover:bg-primary/20 focus:outline-none focus:ring-1 focus:ring-primary/30"
          >
            <X size={10} aria-hidden="true" />
          </button>
        )}
      </span>
    );
  }

  // ---- contact_reference (single) ----------------------------------------

  function ContactReferenceSingle() {
    const contactId = typeof value === 'number' ? value : null;

    function handleSelect(member: Member) {
      onChange(member.contact_id); // number, per C13
      setPickerOpen(false);
    }

    return (
      <>
        <div className="flex min-h-[44px] flex-wrap items-center gap-2 rounded-xl border border-border bg-background px-3 py-2">
          {contactId !== null ? (
            <Chip
              label={`Contact #${contactId}`}
              onRemove={() => onChange(null)}
            />
          ) : (
            <button
              id={inputId}
              type="button"
              disabled={disabled}
              onClick={() => setPickerOpen(true)}
              aria-describedby={describedBy}
              className="text-sm text-foreground/50 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-primary/30 rounded"
            >
              Select contact…
            </button>
          )}
          {contactId !== null && !disabled && (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              className="ml-auto text-xs text-foreground/50 hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 rounded"
            >
              Change
            </button>
          )}
        </div>
        {pickerOpen && (
          <ContactPickerModal
            onSelect={handleSelect}
            onClose={() => setPickerOpen(false)}
          />
        )}
      </>
    );
  }

  // ---- contact_reference (multi) -----------------------------------------

  function ContactReferenceMulti() {
    const ids = asNumberArray(value);

    function handleSelect(member: Member) {
      if (!ids.includes(member.contact_id)) {
        onChange([...ids, member.contact_id]);
      }
      setPickerOpen(false);
    }

    function removeId(id: number) {
      onChange(ids.filter((x) => x !== id));
    }

    return (
      <>
        <div className="flex min-h-[44px] flex-wrap items-center gap-2 rounded-xl border border-border bg-background px-3 py-2">
          {ids.map((id) => (
            <Chip
              key={id}
              label={`Contact #${id}`}
              onRemove={() => removeId(id)}
            />
          ))}
          {!disabled && (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              aria-label="Add contact"
              className="rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-foreground/50 hover:border-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              + Add
            </button>
          )}
        </div>
        {pickerOpen && (
          <ContactPickerModal
            onSelect={handleSelect}
            onClose={() => setPickerOpen(false)}
          />
        )}
      </>
    );
  }

  // ---- multiselect / select(is_multi=true) chip list ----------------------

  function MultiSelectInput() {
    const selected = asStringArray(value);
    const available = (field.options as OptionItem[]).filter(
      (opt) => !selected.includes(opt.value)
    );

    function addOption(optValue: string) {
      onChange([...selected, optValue]);
    }

    function removeOption(optValue: string) {
      onChange(selected.filter((v) => v !== optValue));
    }

    return (
      <div className="space-y-2">
        <div className="flex min-h-[44px] flex-wrap items-center gap-2 rounded-xl border border-border bg-background px-3 py-2">
          {selected.map((v) => {
            const opt = (field.options as OptionItem[]).find((o) => o.value === v);
            return (
              <Chip
                key={v}
                label={opt?.label ?? v}
                onRemove={() => removeOption(v)}
              />
            );
          })}
          {selected.length === 0 && (
            <span className="text-sm text-foreground/50">None selected</span>
          )}
        </div>
        {!disabled && available.length > 0 && (
          <select
            aria-label={`Add option for ${field.label}`}
            disabled={disabled}
            className={baseInputClass}
            value=""
            onChange={(e) => {
              if (e.target.value) addOption(e.target.value);
            }}
          >
            <option value="" disabled>
              Add option…
            </option>
            {available.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        )}
      </div>
    );
  }

  // ---- single select ------------------------------------------------------

  function SingleSelect() {
    const stringVal = typeof value === 'string' ? value : '';
    return (
      <select
        id={inputId}
        disabled={disabled}
        value={stringVal}
        onChange={(e) => onChange(e.target.value || null)}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        aria-required={field.is_required}
        className={baseInputClass + ' min-h-[44px]'}
      >
        <option value="">Select…</option>
        {(field.options as OptionItem[]).map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  }

  // ---- render by data_type ------------------------------------------------

  function renderInput() {
    const { data_type, is_multi } = field;

    if (data_type === 'text') {
      return (
        <input
          id={inputId}
          type="text"
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-required={field.is_required}
          className={baseInputClass + ' min-h-[44px]'}
        />
      );
    }

    if (data_type === 'textarea') {
      return (
        <textarea
          id={inputId}
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-required={field.is_required}
          rows={3}
          className={baseInputClass + ' resize-y'}
        />
      );
    }

    if (data_type === 'number') {
      return (
        <input
          id={inputId}
          type="number"
          disabled={disabled}
          value={typeof value === 'number' ? value : ''}
          onChange={(e) => {
            const n = e.target.value === '' ? null : Number(e.target.value);
            onChange(n);
          }}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-required={field.is_required}
          className={baseInputClass + ' min-h-[44px]'}
        />
      );
    }

    if (data_type === 'date') {
      return (
        <input
          id={inputId}
          type="date"
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value || null)}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-required={field.is_required}
          className={baseInputClass + ' min-h-[44px]'}
        />
      );
    }

    if (data_type === 'checkbox') {
      return (
        <div className="flex min-h-[44px] items-center">
          <input
            id={inputId}
            type="checkbox"
            disabled={disabled}
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            aria-labelledby={labelId}
            aria-describedby={describedBy}
            className="h-5 w-5 rounded border-border text-primary focus:ring-2 focus:ring-primary/30"
          />
        </div>
      );
    }

    if (data_type === 'select') {
      if (is_multi) return <MultiSelectInput />;
      return <SingleSelect />;
    }

    if (data_type === 'multiselect') {
      return <MultiSelectInput />;
    }

    if (data_type === 'contact_reference') {
      if (is_multi) return <ContactReferenceMulti />;
      return <ContactReferenceSingle />;
    }

    // Fallback for any unrecognised data_type — render a plain text input
    return (
      <input
        id={inputId}
        type="text"
        disabled={disabled}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value)}
        className={baseInputClass + ' min-h-[44px]'}
      />
    );
  }

  // Composite types (contact_reference, multiselect, select[is_multi]) manage their
  // own focus targets internally — do NOT set htmlFor on the outer label so the
  // label text doesn't clobber the accessible name of the inner button/select.
  const isComposite =
    field.data_type === 'contact_reference' ||
    field.data_type === 'multiselect' ||
    (field.data_type === 'select' && field.is_multi);

  return (
    <div className="space-y-1">
      <label
        id={labelId}
        htmlFor={isComposite ? undefined : inputId}
        className="block text-sm font-medium text-foreground"
      >
        {field.label}
        {field.is_required && (
          <span className="ml-1 text-destructive" aria-hidden="true">
            *
          </span>
        )}
      </label>

      {renderInput()}

      {field.help_text && (
        <p id={helpId} className="mt-1 text-sm text-foreground/50">
          {field.help_text}
        </p>
      )}

      {error && (
        <p id={errorId} role="alert" className="mt-1 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ContactPickerModal — adapts MemberSearchModal to the contact_reference API.
// MemberSearchModal is task-centric (mode + onSelect(Member)), so we wrap it:
//   - pass mode='edit' (semantically: link to an existing member)
//   - extract member.contact_id (number) before calling the parent onChange
// The modal's own search calls /members and reads res.data defensively per C16.
// ---------------------------------------------------------------------------

interface ContactPickerModalProps {
  onSelect: (member: Member) => void;
  onClose: () => void;
}

function ContactPickerModal({ onSelect, onClose }: ContactPickerModalProps) {
  return (
    <MemberSearchModal
      mode="edit"
      onSelect={onSelect}
      onClose={onClose}
    />
  );
}
