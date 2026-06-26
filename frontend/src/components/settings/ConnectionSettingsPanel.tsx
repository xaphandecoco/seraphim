import { useState } from 'react';
import { toast } from 'sonner';
import { Database, Settings2, Plug, Server } from 'lucide-react';
import {
  buildPostgresUrl,
  parsePostgresUrl,
  buildRedisUrl,
  parseRedisUrl,
} from '@/services/connectionUrl';
import { api } from '@/services/api';
import type { SettingItem } from '@/types';

interface ConnectionSettingsPanelProps {
  settings: SettingItem[];
  onSaved: () => void;
}

const inputClass =
  'h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-foreground/40';
const labelClass = 'block text-xs font-semibold text-foreground/60 mb-1';

export function ConnectionSettingsPanel({
  settings,
  onSaved,
}: ConnectionSettingsPanelProps) {
  const settingMap = Object.fromEntries(
    settings.map((s) => [s.key, s.value?.value]),
  );

  const initialDb = (() => {
    const raw = settingMap['database_url'];
    return raw ? parsePostgresUrl(String(raw)) : { host: '', port: '5432', name: '', username: '', password: '', extra: '' };
  })();
  const initialRedis = (() => {
    const raw = settingMap['redis_url'];
    return raw ? parseRedisUrl(String(raw)) : { host: '', port: '6379', db: '0', password: '' };
  })();

  const [showEdit, setShowEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dbFields, setDbFields] = useState(initialDb);
  const [redisFields, setRedisFields] = useState(initialRedis);
  const [comprefaceDetectKey, setComprefaceDetectKey] = useState('');
  const [comprefaceRecognizeKey, setComprefaceRecognizeKey] = useState('');
  const [testResult, setTestResult] = useState<{
    database_ok?: boolean;
    database_message?: string;
    redis_ok?: boolean;
    redis_message?: string;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  const save = async () => {
    const database_url = buildPostgresUrl(
      dbFields.host,
      dbFields.port,
      dbFields.name,
      dbFields.username,
      dbFields.password,
      dbFields.extra,
    );
    const redis_url = buildRedisUrl(
      redisFields.host,
      redisFields.port,
      redisFields.db,
      redisFields.password,
    );
    setSaving(true);
    try {
      const payload: Record<string, string> = { database_url, redis_url };
      if (comprefaceDetectKey) payload.compreface_detect_api_key = comprefaceDetectKey;
      if (comprefaceRecognizeKey) payload.compreface_recognize_api_key = comprefaceRecognizeKey;
      await api.put('/settings', { settings: payload });
      setComprefaceDetectKey('');
      setComprefaceRecognizeKey('');
      setShowEdit(false);
      onSaved();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to save system settings');
    } finally {
      setSaving(false);
    }
  };

  const testConnections = async () => {
    const database_url = buildPostgresUrl(
      dbFields.host,
      dbFields.port,
      dbFields.name,
      dbFields.username,
      dbFields.password,
      dbFields.extra,
    );
    const redis_url = buildRedisUrl(
      redisFields.host,
      redisFields.port,
      redisFields.db,
      redisFields.password,
    );
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post('/setup/test-connection', { database_url, redis_url });
      setTestResult(res.data);
    } catch (err: any) {
      setTestResult({
        database_ok: false,
        database_message: err.response?.data?.detail || 'Test request failed',
        redis_ok: false,
        redis_message: '',
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="mb-4 space-y-1">
      <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">System</h2>
      <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
        {!showEdit ? (
          <>
            <div className="space-y-2">
              {settings.map((s) => (
                <div key={s.key} className="flex items-center justify-between text-sm">
                  <span className="text-foreground/50">{s.key}</span>
                  <span className="font-mono text-xs text-foreground">
                    {s.sensitive ? '********' : String(s.value?.value ?? '')}
                  </span>
                </div>
              ))}
            </div>
            <button
              onClick={() => setShowEdit(true)}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-background py-2 text-xs font-semibold text-foreground transition-all hover:bg-primary/20 active:scale-[0.98]"
            >
              <Settings2 size={14} />
              Edit Connection Settings
            </button>
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
              <Database size={16} />
              Database Connection
            </div>
            <div>
              <label className={labelClass}>Database Host / URL</label>
              <input
                type="text"
                value={dbFields.host}
                onChange={(e) => setDbFields({ ...dbFields, host: e.target.value })}
                placeholder="e.g. postgres"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Database Port</label>
              <input
                type="text"
                value={dbFields.port}
                onChange={(e) => setDbFields({ ...dbFields, port: e.target.value })}
                placeholder="5432"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Database Name</label>
              <input
                type="text"
                value={dbFields.name}
                onChange={(e) => setDbFields({ ...dbFields, name: e.target.value })}
                placeholder="seraphim_attendance"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Username</label>
              <input
                type="text"
                value={dbFields.username}
                onChange={(e) => setDbFields({ ...dbFields, username: e.target.value })}
                placeholder="seraphim"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Password</label>
              <input
                type="password"
                value={dbFields.password}
                onChange={(e) => setDbFields({ ...dbFields, password: e.target.value })}
                placeholder="Database password"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Additional Connection Parameters (optional)</label>
              <input
                type="text"
                value={dbFields.extra}
                onChange={(e) => setDbFields({ ...dbFields, extra: e.target.value })}
                placeholder="sslmode=require"
                className={inputClass}
              />
            </div>

            <div className="border-t border-border pt-3">
              <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                <Database size={16} />
                Redis Cache
              </div>
            </div>
            <div>
              <label className={labelClass}>Redis Host</label>
              <input
                type="text"
                value={redisFields.host}
                onChange={(e) => setRedisFields({ ...redisFields, host: e.target.value })}
                placeholder="e.g. redis"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Redis Port</label>
              <input
                type="text"
                value={redisFields.port}
                onChange={(e) => setRedisFields({ ...redisFields, port: e.target.value })}
                placeholder="6379"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Redis DB Index</label>
              <input
                type="text"
                value={redisFields.db}
                onChange={(e) => setRedisFields({ ...redisFields, db: e.target.value })}
                placeholder="0"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Redis Password (optional)</label>
              <input
                type="password"
                value={redisFields.password}
                onChange={(e) => setRedisFields({ ...redisFields, password: e.target.value })}
                placeholder="Leave blank if no auth"
                className={inputClass}
              />
            </div>

            <div className="border-t border-border pt-3">
              <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                <Server size={16} />
                CompreFace API Keys
              </div>
              <p className="mt-1 text-xs text-foreground/50">
                Leave blank to keep the existing value. CompreFace issues one UUID key per service.
              </p>
            </div>
            <div>
              <label htmlFor="settings-compreface-detect-key" className={labelClass}>
                Detection Service API Key
              </label>
              <input
                id="settings-compreface-detect-key"
                type="password"
                value={comprefaceDetectKey}
                onChange={(e) => setComprefaceDetectKey(e.target.value)}
                placeholder="Detection service UUID key"
                className={inputClass}
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor="settings-compreface-recognize-key" className={labelClass}>
                Recognition Service API Key
              </label>
              <input
                id="settings-compreface-recognize-key"
                type="password"
                value={comprefaceRecognizeKey}
                onChange={(e) => setComprefaceRecognizeKey(e.target.value)}
                placeholder="Recognition service UUID key"
                className={inputClass}
                autoComplete="off"
              />
            </div>

            <button
              onClick={testConnections}
              disabled={testing}
              className="flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-border bg-card text-xs font-semibold text-foreground transition-all hover:bg-background active:scale-[0.98] disabled:opacity-50"
            >
              <Plug size={14} />
              {testing ? 'Testing...' : 'Test Connections'}
            </button>

            {testResult && (
              <div className="space-y-2 rounded-xl bg-background p-3 text-xs">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${testResult.database_ok ? 'bg-green-500' : 'bg-red-500'}`} />
                  <span className="font-semibold text-foreground">Postgres</span>
                  <span className="text-foreground/50">{testResult.database_message}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${testResult.redis_ok ? 'bg-green-500' : 'bg-red-500'}`} />
                  <span className="font-semibold text-foreground">Redis</span>
                  <span className="text-foreground/50">{testResult.redis_message}</span>
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                onClick={() => {
                  setShowEdit(false);
                  setComprefaceDetectKey('');
                  setComprefaceRecognizeKey('');
                }}
                className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground transition-all hover:bg-background active:scale-[0.98]"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
