import { useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/services/api';
import type { SettingItem } from '@/types';

const EDITABLE_TUNABLES: Array<{
  key: string;
  label: string;
  type: 'number' | 'float' | 'boolean' | 'text';
  description: string;
}> = [
  { key: 'similarity_threshold_high', label: 'Auto-log threshold', type: 'float', description: 'Similarity >= this -> auto-logged (0-1, e.g. 0.98)' },
  { key: 'similarity_threshold_medium', label: 'Single-approval threshold', type: 'float', description: 'Similarity >= this -> 1-volunteer review (0-1, e.g. 0.91)' },
  { key: 'queue_hard_limit', label: 'Queue hard limit', type: 'number', description: 'Stop ingestion when pending tasks exceed this' },
  { key: 'queue_resume_limit', label: 'Queue resume limit', type: 'number', description: 'Resume ingestion when pending falls below this' },
  { key: 'dedup_window_seconds', label: 'Dedup window (s)', type: 'number', description: 'Skip faces already detected within this window' },
  { key: 'task_expiry_days', label: 'Task expiry (days)', type: 'number', description: 'Unresolved tasks are expired after this many days' },
  { key: 'face_retention_days', label: 'Face retention (days)', type: 'number', description: 'Untrained face snapshots deleted after this many days' },
  { key: 'access_token_expire_minutes', label: 'Access token expiry (min)', type: 'number', description: 'JWT access token lifetime in minutes' },
  { key: 'refresh_token_expire_days', label: 'Refresh token expiry (days)', type: 'number', description: 'Refresh cookie lifetime in days' },
  { key: 'allowed_domain', label: 'Allowed OAuth domain', type: 'text', description: 'Google OAuth restricted to this email domain' },
  { key: 'enable_google_oauth', label: 'Enable Google OAuth', type: 'boolean', description: 'Allow volunteers to sign in with Google' },
];

interface TunablesPanelProps {
  settings: SettingItem[];
  onSaved: () => void;
}

const inputCls =
  'h-9 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30';

export function TunablesPanel({ settings, onSaved }: TunablesPanelProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const settingMap = Object.fromEntries(settings.map((s) => [s.key, s.value?.value]));
  const [values, setValues] = useState<Record<string, any>>({});

  const startEdit = () => {
    const initial: Record<string, any> = {};
    EDITABLE_TUNABLES.forEach((t) => {
      initial[t.key] = settingMap[t.key] ?? '';
    });
    setValues(initial);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload: Record<string, any> = {};
      EDITABLE_TUNABLES.forEach((t) => {
        const v = values[t.key];
        if (t.type === 'number') payload[t.key] = Number(v);
        else if (t.type === 'float') payload[t.key] = parseFloat(v);
        else if (t.type === 'boolean') payload[t.key] = Boolean(v);
        else payload[t.key] = v;
      });
      await api.put('/settings', { settings: payload });
      toast.success('Settings saved');
      setEditing(false);
      onSaved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mb-4 space-y-1">
      <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
        Recognition & Queue
      </h2>
      <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
        {!editing ? (
          <>
            <div className="space-y-2">
              {EDITABLE_TUNABLES.filter((t) => settingMap[t.key] !== undefined).map((t) => (
                <div key={t.key} className="flex items-center justify-between text-xs">
                  <span className="text-foreground/60">{t.label}</span>
                  <span className="font-mono font-semibold text-foreground">
                    {String(settingMap[t.key] ?? '—')}
                  </span>
                </div>
              ))}
            </div>
            <button
              onClick={startEdit}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-background py-2 text-xs font-semibold text-foreground hover:bg-primary/10"
            >
              Edit Tunables
            </button>
          </>
        ) : (
          <div className="space-y-3">
            {EDITABLE_TUNABLES.map((t) => (
              <div key={t.key}>
                <label
                  htmlFor={`tunable-${t.key}`}
                  className="mb-1 block text-xs font-semibold text-foreground/60"
                >
                  {t.label}
                </label>
                <p className="mb-1 text-[10px] text-foreground/40">{t.description}</p>
                {t.type === 'boolean' ? (
                  <select
                    id={`tunable-${t.key}`}
                    value={String(values[t.key])}
                    onChange={(e) =>
                      setValues({ ...values, [t.key]: e.target.value === 'true' })
                    }
                    className={inputCls}
                  >
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                ) : (
                  <input
                    id={`tunable-${t.key}`}
                    type={t.type === 'number' || t.type === 'float' ? 'number' : 'text'}
                    step={t.type === 'float' ? '0.01' : undefined}
                    value={String(values[t.key] ?? '')}
                    onChange={(e) => setValues({ ...values, [t.key]: e.target.value })}
                    className={inputCls}
                  />
                )}
              </div>
            ))}
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => setEditing(false)}
                className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground hover:bg-background"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm disabled:opacity-50 hover:bg-primary/85"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
