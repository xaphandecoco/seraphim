import { useState } from 'react';
import { toast } from 'sonner';
import { updateSetting } from '@/services/settings';

interface SettingKeyEditorProps {
  settingKey: string;
  label: string;
  currentValue?: string;
  sensitive?: boolean;
  placeholder?: string;
  onSaved?: () => void;
}

export function SettingKeyEditor({
  settingKey,
  label,
  currentValue = '',
  sensitive = false,
  placeholder,
  onSaved,
}: SettingKeyEditorProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  const inputClass =
    'h-9 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30';

  const handleSave = async () => {
    if (!value.trim()) return;
    setSaving(true);
    try {
      await updateSetting(settingKey, value.trim());
      toast.success(`${label} saved`);
      setValue('');
      setEditing(false);
      onSaved?.();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to save ${label}`);
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setValue('');
    setEditing(false);
  };

  const displayValue = (() => {
    if (!currentValue) return 'Not set';
    if (sensitive) return '●●●●●●●●';
    return currentValue;
  })();

  if (!editing) {
    return (
      <div className="flex items-center justify-between gap-3 text-xs">
        <div className="min-w-0 flex-1">
          <span className="block font-medium text-foreground/70">{label}</span>
          <span
            className={`block font-mono ${
              currentValue ? 'text-foreground' : 'text-foreground/30'
            }`}
          >
            {displayValue}
          </span>
        </div>
        <button
          onClick={() => setEditing(true)}
          className="shrink-0 rounded-lg border border-border bg-background px-2 py-1 text-xs font-semibold text-foreground hover:bg-primary/10"
        >
          {currentValue ? 'Change' : 'Set'}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-foreground/70">
        {sensitive ? `New ${label} (leave blank to keep existing)` : label}
      </label>
      <input
        type={sensitive ? 'password' : 'text'}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder || (sensitive ? 'Enter new value…' : 'Enter value…')}
        className={inputClass}
        autoComplete="off"
      />
      <div className="flex gap-2">
        <button
          onClick={handleCancel}
          className="flex h-8 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground hover:bg-background"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving || !value.trim()}
          className="flex h-8 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm disabled:opacity-50 hover:bg-primary/85"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
