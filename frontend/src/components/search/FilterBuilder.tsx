import { useState, useCallback } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import type { CriteriaNode, CriteriaGroup, CriteriaLeaf, FieldSpec } from '@/types/search';
import { isCriteriaGroup } from '@/types/search';
import { FieldPicker } from './FieldPicker';
import { OperatorSelect } from './OperatorSelect';
import { ContactPickerModal } from '@/components/tasks/ContactPickerModal';
import type { Member } from '@/types';

// Operators that do not take a value
const NO_VALUE_OPS = new Set(['is_set', 'is_empty']);

// ---------- Value input ------------------------------------------------------

interface ValueInputProps {
  field: FieldSpec | undefined;
  op: string;
  value: unknown;
  onChange: (v: unknown) => void;
}

function ValueInput({ field, op, value, onChange }: ValueInputProps) {
  const [showPicker, setShowPicker] = useState(false);

  if (!field || NO_VALUE_OPS.has(op)) return null;

  // contact_reference: open ContactPickerModal to pick a contact id
  if (field.key === 'contact_reference') {
    const displayVal = value !== undefined && value !== null ? `ID: ${value}` : '';
    return (
      <>
        <button
          type="button"
          onClick={() => setShowPicker(true)}
          className="h-9 min-w-[120px] rounded-lg border border-border bg-card px-3 text-left text-sm text-foreground hover:bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          {displayVal || (
            <span className="text-foreground/40">Pick contact…</span>
          )}
        </button>
        {showPicker && (
          <ContactPickerModal
            mode="edit"
            onSelect={(member: Member) => {
              onChange(member.contact_id);
              setShowPicker(false);
            }}
            onClose={() => setShowPicker(false)}
          />
        )}
      </>
    );
  }

  // bool: true / false toggle
  if (field.type === 'bool') {
    return (
      <select
        value={String(value ?? 'true')}
        onChange={(e) => onChange(e.target.value === 'true')}
        aria-label="Value"
        className="h-9 rounded-lg border border-border bg-card px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
      >
        <option value="true">True</option>
        <option value="false">False</option>
      </select>
    );
  }

  // enum: single select from options
  if (field.type === 'enum' && field.options) {
    return (
      <select
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Value"
        className="h-9 rounded-lg border border-border bg-card px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
      >
        <option value="" disabled>
          Select…
        </option>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }

  // multiselect: toggle chips
  if (field.type === 'multiselect' && field.options) {
    const selected = Array.isArray(value) ? (value as string[]) : [];
    const toggle = (v: string) => {
      onChange(
        selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v],
      );
    };
    return (
      <div className="flex flex-wrap gap-1">
        {field.options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={selected.includes(o.value)}
            onClick={() => toggle(o.value)}
            className={`rounded-full border px-2 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
              selected.includes(o.value)
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    );
  }

  // date / datetime
  if (field.type === 'date' || field.type === 'datetime') {
    return (
      <input
        type={field.type === 'datetime' ? 'datetime-local' : 'date'}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value || undefined)}
        aria-label="Value"
        className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
      />
    );
  }

  // int / number
  if (field.type === 'int' || field.type === 'number') {
    return (
      <input
        type="number"
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : Number(e.target.value))
        }
        aria-label="Value"
        className="h-9 w-28 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
      />
    );
  }

  // Default: text
  return (
    <input
      type="text"
      value={String(value ?? '')}
      onChange={(e) => onChange(e.target.value || undefined)}
      aria-label="Value"
      className="h-9 min-w-[120px] rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
    />
  );
}

// ---------- Leaf row ---------------------------------------------------------

interface LeafRowProps {
  leaf: CriteriaLeaf;
  fields: FieldSpec[];
  onChange: (leaf: CriteriaLeaf) => void;
  onRemove: () => void;
}

function LeafRow({ leaf, fields, onChange, onRemove }: LeafRowProps) {
  const fieldSpec = fields.find((f) => f.key === leaf.field);

  const setField = (key: string) => {
    const spec = fields.find((f) => f.key === key);
    onChange({ field: key, op: spec?.ops[0] ?? '', value: undefined });
  };

  const setOp = (op: string) => {
    onChange({ ...leaf, op, value: NO_VALUE_OPS.has(op) ? undefined : leaf.value });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-background p-2">
      <FieldPicker fields={fields} value={leaf.field} onChange={setField} />
      <OperatorSelect field={fieldSpec} value={leaf.op} onChange={setOp} />
      <ValueInput
        field={fieldSpec}
        op={leaf.op}
        value={leaf.value}
        onChange={(v) => onChange({ ...leaf, value: v })}
      />
      <button
        type="button"
        onClick={onRemove}
        aria-label="Remove condition"
        className="ml-auto flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-card hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

// ---------- FilterBuilder (recursive) ----------------------------------------

interface FilterBuilderProps {
  value: CriteriaGroup;
  fields: FieldSpec[];
  onChange: (group: CriteriaGroup) => void;
  /** Provided when this group can be removed (not the root) */
  onRemove?: () => void;
  depth?: number;
}

export function FilterBuilder({
  value,
  fields,
  onChange,
  onRemove,
  depth = 0,
}: FilterBuilderProps) {
  const toggleLogic = () => {
    onChange({ ...value, logic: value.logic === 'and' ? 'or' : 'and' });
  };

  const addLeaf = () => {
    const firstField = fields[0];
    const newLeaf: CriteriaLeaf = {
      field: firstField?.key ?? '',
      op: firstField?.ops[0] ?? '',
    };
    onChange({ ...value, conditions: [...value.conditions, newLeaf] });
  };

  const addGroup = () => {
    const newGroup: CriteriaGroup = { logic: 'and', conditions: [] };
    onChange({ ...value, conditions: [...value.conditions, newGroup] });
  };

  const updateCondition = useCallback(
    (index: number, node: CriteriaNode) => {
      onChange({
        ...value,
        conditions: value.conditions.map((c, i) => (i === index ? node : c)),
      });
    },
    [value, onChange],
  );

  const removeCondition = useCallback(
    (index: number) => {
      onChange({
        ...value,
        conditions: value.conditions.filter((_, i) => i !== index),
      });
    },
    [value, onChange],
  );

  const borderColor =
    depth === 0
      ? 'border-border'
      : depth === 1
        ? 'border-primary/30'
        : 'border-foreground/20';

  return (
    <div className={`rounded-xl border ${borderColor} bg-card p-3 space-y-2`}>
      {/* Logic toggle + optional remove */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggleLogic}
          aria-label={`Logic: ${value.logic.toUpperCase()}. Click to toggle.`}
          className="rounded-lg border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-bold text-primary hover:bg-primary/20 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {value.logic.toUpperCase()}
        </button>
        <span className="text-xs text-foreground/50">
          Match {value.logic === 'and' ? 'all' : 'any'} of:
        </span>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label="Remove group"
            className="ml-auto flex h-7 w-7 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Conditions */}
      {value.conditions.length === 0 && (
        <p className="py-2 text-center text-xs text-foreground/40">
          No conditions yet — add one below.
        </p>
      )}

      {value.conditions.map((cond, i) =>
        isCriteriaGroup(cond) ? (
          <FilterBuilder
            key={i}
            value={cond}
            fields={fields}
            onChange={(g) => updateCondition(i, g)}
            onRemove={() => removeCondition(i)}
            depth={depth + 1}
          />
        ) : (
          <LeafRow
            key={i}
            leaf={cond as CriteriaLeaf}
            fields={fields}
            onChange={(l) => updateCondition(i, l)}
            onRemove={() => removeCondition(i)}
          />
        ),
      )}

      {/* Add buttons */}
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={addLeaf}
          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-foreground/70 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Plus size={12} aria-hidden="true" /> Add condition
        </button>
        <button
          type="button"
          onClick={addGroup}
          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-foreground/70 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Plus size={12} aria-hidden="true" /> Add group
        </button>
      </div>
    </div>
  );
}
