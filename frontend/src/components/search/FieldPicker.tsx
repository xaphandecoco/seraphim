import { useMemo } from 'react';
import type { FieldSpec, FieldKind } from '@/types/search';

interface FieldPickerProps {
  fields: FieldSpec[];
  value: string;
  onChange: (key: string) => void;
}

const KIND_LABELS: Record<FieldKind, string> = {
  core: 'Core Fields',
  derived: 'Derived Fields',
  custom: 'Custom Fields',
};

const KIND_ORDER: FieldKind[] = ['core', 'derived', 'custom'];

export function FieldPicker({ fields, value, onChange }: FieldPickerProps) {
  const grouped = useMemo(() => {
    const map = new Map<FieldKind, FieldSpec[]>();
    for (const kind of KIND_ORDER) {
      const group = fields.filter((f) => f.kind === kind);
      if (group.length > 0) map.set(kind, group);
    }
    return map;
  }, [fields]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Select field"
      className="h-9 rounded-lg border border-border bg-card px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
    >
      <option value="" disabled>
        Select field…
      </option>
      {KIND_ORDER.filter((k) => grouped.has(k)).map((kind) => (
        <optgroup key={kind} label={KIND_LABELS[kind]}>
          {grouped.get(kind)!.map((f) => (
            <option key={f.key} value={f.key}>
              {f.label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
