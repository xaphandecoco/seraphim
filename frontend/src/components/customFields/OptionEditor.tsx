import { Plus, Trash2 } from 'lucide-react';
import type { OptionItem } from '@/types/customFields';

interface OptionEditorProps {
  options: OptionItem[];
  onChange: (opts: OptionItem[]) => void;
}

export function OptionEditor({ options, onChange }: OptionEditorProps) {
  const addOption = () => {
    onChange([...options, { value: '', label: '' }]);
  };

  const removeOption = (idx: number) => {
    onChange(options.filter((_, i) => i !== idx));
  };

  const updateOption = (idx: number, field: keyof OptionItem, val: string) => {
    onChange(options.map((opt, i) => (i === idx ? { ...opt, [field]: val } : opt)));
  };

  // Find duplicate values for inline error display
  const valueCounts = options.reduce<Record<string, number>>((acc, opt) => {
    if (opt.value) acc[opt.value] = (acc[opt.value] ?? 0) + 1;
    return acc;
  }, {});
  const hasDuplicate = (val: string) => !!val && (valueCounts[val] ?? 0) > 1;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Options</p>
      {options.map((opt, idx) => (
        <div key={idx} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="value (snake_case)"
              value={opt.value}
              onChange={(e) => updateOption(idx, 'value', e.target.value)}
              className="h-9 flex-1 rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
              aria-label={`Option ${idx + 1} value`}
            />
            <input
              type="text"
              placeholder="Label"
              value={opt.label}
              onChange={(e) => updateOption(idx, 'label', e.target.value)}
              className="h-9 flex-1 rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
              aria-label={`Option ${idx + 1} label`}
            />
            <button
              type="button"
              onClick={() => removeOption(idx)}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-foreground/50 hover:bg-red-50 hover:text-red-600 focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label={`Remove option ${idx + 1}`}
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </div>
          {hasDuplicate(opt.value) && (
            <p className="text-xs text-destructive" role="alert">
              Duplicate value — option values must be unique.
            </p>
          )}
        </div>
      ))}
      <button
        type="button"
        onClick={addOption}
        className="flex min-h-[36px] items-center gap-2 rounded-xl border border-dashed border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <Plus size={13} aria-hidden="true" />
        Add option
      </button>
    </div>
  );
}
