import type { FieldSpec } from '@/types/search';

const OP_LABELS: Record<string, string> = {
  eq: 'equals',
  neq: 'not equals',
  lt: 'less than',
  lte: 'less than or equal',
  gt: 'greater than',
  gte: 'greater than or equal',
  contains: 'contains',
  icontains: 'contains (any case)',
  startswith: 'starts with',
  endswith: 'ends with',
  in: 'is one of',
  not_in: 'is not one of',
  is_set: 'is set',
  is_empty: 'is empty',
  between: 'between',
};

interface OperatorSelectProps {
  field: FieldSpec | undefined;
  value: string;
  onChange: (op: string) => void;
}

export function OperatorSelect({ field, value, onChange }: OperatorSelectProps) {
  const ops = field?.ops ?? [];

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={!field}
      aria-label="Select operator"
      className="h-9 rounded-lg border border-border bg-card px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
    >
      <option value="" disabled>
        Op…
      </option>
      {ops.map((op) => (
        <option key={op} value={op}>
          {OP_LABELS[op] ?? op}
        </option>
      ))}
    </select>
  );
}
