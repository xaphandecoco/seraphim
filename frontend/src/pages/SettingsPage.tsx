import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { buildPostgresUrl, parsePostgresUrl, buildRedisUrl, parseRedisUrl } from '@/services/connectionUrl';
import { LogOut, Shield, Users, Camera, Power, AlertTriangle, Database, Settings2, Plug, Upload, Server } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { Camera as CameraType } from '@/types';

interface SettingItem {
  key: string;
  value: { value: any };
  category: string;
  description?: string;
  requires_restart?: boolean;
  sensitive?: boolean;
}

// Tunables that are editable via a structured form (non-sensitive, non-restart)
const EDITABLE_TUNABLES: Array<{ key: string; label: string; type: 'number' | 'float' | 'boolean' | 'text'; description: string }> = [
  { key: 'similarity_threshold_high', label: 'Auto-log threshold', type: 'float', description: 'Similarity ≥ this → auto-logged (0–1, e.g. 0.98)' },
  { key: 'similarity_threshold_medium', label: 'Single-approval threshold', type: 'float', description: 'Similarity ≥ this → 1-volunteer review (0–1, e.g. 0.91)' },
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

function TunablesEditor({ settings, onSaved }: { settings: SettingItem[]; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const settingMap = Object.fromEntries(settings.map((s) => [s.key, s.value?.value]));
  const [values, setValues] = useState<Record<string, any>>({});

  const startEdit = () => {
    const initial: Record<string, any> = {};
    EDITABLE_TUNABLES.forEach((t) => { initial[t.key] = settingMap[t.key] ?? ''; });
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

  const inputCls = 'h-9 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30';

  return (
    <div className="mb-4 space-y-1">
      <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">Recognition & Queue</h2>
      <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
        {!editing ? (
          <>
            <div className="space-y-2">
              {EDITABLE_TUNABLES.filter((t) => settingMap[t.key] !== undefined).map((t) => (
                <div key={t.key} className="flex items-center justify-between text-xs">
                  <span className="text-foreground/60">{t.label}</span>
                  <span className="font-mono font-semibold text-foreground">{String(settingMap[t.key] ?? '—')}</span>
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
                <label htmlFor={`tunable-${t.key}`} className="mb-1 block text-xs font-semibold text-foreground/60">{t.label}</label>
                <p className="mb-1 text-[10px] text-foreground/40">{t.description}</p>
                {t.type === 'boolean' ? (
                  <select
                    id={`tunable-${t.key}`}
                    value={String(values[t.key])}
                    onChange={(e) => setValues({ ...values, [t.key]: e.target.value === 'true' })}
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
              <button onClick={() => setEditing(false)} className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground hover:bg-background">Cancel</button>
              <button onClick={save} disabled={saving} className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm disabled:opacity-50 hover:bg-primary/85">
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function SettingsPage() {
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);
  const user = useAuthStore((s) => s.user);
  const [settings, setSettings] = useState<SettingItem[]>([]);
  const [cameras, setCameras] = useState<CameraType[]>([]);
  const [safeMode, setSafeMode] = useState(false);
  const [darkMode, setDarkMode] = useState(() => {
    const stored = localStorage.getItem('seraphim-theme');
    if (stored) return stored === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });
  const [loading, setLoading] = useState(true);
  const [showCameraForm, setShowCameraForm] = useState(false);
  const [cameraForm, setCameraForm] = useState({
    name: '',
    rtsp_url: '',
    zone_label: '',
    fps: 1,
    enable_health_check: true,
  });

  // System config editing state
  const [showSystemEdit, setShowSystemEdit] = useState(false);
  const [systemSaving, setSystemSaving] = useState(false);
  const [dbFields, setDbFields] = useState({
    host: '', port: '5432', name: '', username: '', password: '', extra: '',
  });
  const [redisFields, setRedisFields] = useState({
    host: '', port: '6379', db: '0', password: '',
  });
  // CompreFace service-specific key editing (empty = keep existing DB value)
  const [comprefaceDetectKey, setComprefaceDetectKey] = useState('');
  const [comprefaceRecognizeKey, setComprefaceRecognizeKey] = useState('');

  const [testResult, setTestResult] = useState<{
    database_ok?: boolean;
    database_message?: string;
    redis_ok?: boolean;
    redis_message?: string;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  // Face upload state
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{
    faces_detected: number;
    quality_passed: number;
    quality_failed: number;
    tasks_created: number;
    auto_logged: number;
    skipped: number;
    deduplicated: number;
  } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [previewCamera, setPreviewCamera] = useState<{ id: number; name: string } | null>(null);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    try {
      const [settingsRes, camerasRes] = await Promise.all([
        api.get('/settings'),
        api.get('/cameras'),
      ]);
      const fetchedSettings: SettingItem[] = settingsRes.data.settings || [];
      setSettings(fetchedSettings);
      setCameras(camerasRes.data || []);
      const safeSetting = fetchedSettings.find((s) => s.key === 'safe_mode');
      setSafeMode(safeSetting?.value?.value === true);

      // Parse existing URLs into fields
      const dbSetting = fetchedSettings.find((s) => s.key === 'database_url');
      if (dbSetting?.value?.value) {
        setDbFields(parsePostgresUrl(String(dbSetting.value.value)));
      }
      const redisSetting = fetchedSettings.find((s) => s.key === 'redis_url');
      if (redisSetting?.value?.value) {
        setRedisFields(parseRedisUrl(String(redisSetting.value.value)));
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

  const handleLogout = async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // best-effort; clear local state regardless
    }
    logout();
    navigate('/login');
  };

  const toggleDarkMode = () => {
    const next = !darkMode;
    setDarkMode(next);
    localStorage.setItem('seraphim-theme', next ? 'dark' : 'light');
    document.documentElement.classList.toggle('dark', next);
  };

  const toggleSafeMode = async () => {
    try {
      const res = await api.post(`/settings/safe-mode?enabled=${!safeMode}`);
      setSafeMode(res.data.safe_mode);
      toast.success(`Safe mode ${res.data.safe_mode ? 'enabled' : 'disabled'}`);
    } catch {
      toast.error('Failed to toggle safe mode');
    }
  };

  const addCamera = async () => {
    try {
      await api.post('/cameras', cameraForm);
      setCameraForm({ name: '', rtsp_url: '', zone_label: '', fps: 1, enable_health_check: true });
      setShowCameraForm(false);
      fetchData();
      toast.success('Camera added');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to add camera');
    }
  };

  const deleteCamera = async (id: number) => {
    try {
      await api.delete(`/cameras/${id}`);
      setConfirmDelete(null);
      fetchData();
      toast.success('Camera deleted');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to delete camera');
    }
  };

  const openPreview = async (id: number, name: string) => {
    setPreviewCamera({ id, name });
    setPreviewSrc(null);
    setPreviewLoading(true);
    try {
      const res = await api.get(`/cameras/${id}/preview`, { responseType: 'blob' });
      setPreviewSrc(URL.createObjectURL(res.data));
    } catch {
      toast.error('Could not capture preview frame');
      setPreviewCamera(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const reconnectCamera = async (id: number) => {
    try {
      await api.post(`/cameras/${id}/reconnect`);
      toast.success('Reconnect initiated');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to reconnect');
    }
  };

  const saveSystemSettings = async () => {
    const database_url = buildPostgresUrl(
      dbFields.host,
      dbFields.port,
      dbFields.name,
      dbFields.username,
      dbFields.password,
      dbFields.extra
    );
    const redis_url = buildRedisUrl(
      redisFields.host,
      redisFields.port,
      redisFields.db,
      redisFields.password
    );

    setSystemSaving(true);
    try {
      const payload: Record<string, string> = { database_url, redis_url };
      // Only include CompreFace keys when the operator typed a new value.
      // Leaving the field blank preserves the existing masked value in the DB.
      if (comprefaceDetectKey) payload.compreface_detect_api_key = comprefaceDetectKey;
      if (comprefaceRecognizeKey) payload.compreface_recognize_api_key = comprefaceRecognizeKey;
      await api.put('/settings', { settings: payload });
      setComprefaceDetectKey('');
      setComprefaceRecognizeKey('');
      setShowSystemEdit(false);
      fetchData();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to save system settings');
    } finally {
      setSystemSaving(false);
    }
  };

  const runConnectionTest = async () => {
    const database_url = buildPostgresUrl(
      dbFields.host,
      dbFields.port,
      dbFields.name,
      dbFields.username,
      dbFields.password,
      dbFields.extra
    );
    const redis_url = buildRedisUrl(
      redisFields.host,
      redisFields.port,
      redisFields.db,
      redisFields.password
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

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    setUploadFile(file);
    setUploadResult(null);
    setUploadError(null);
  };

  const handleUpload = async () => {
    if (!uploadFile) return;
    setUploading(true);
    setUploadResult(null);
    setUploadError(null);
    try {
      const formData = new FormData();
      formData.append('file', uploadFile);
      const res = await api.post('/uploads/faces', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadResult(res.data);
      setUploadFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } catch (err: any) {
      setUploadError(err.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-foreground/50">
        Loading...
      </div>
    );
  }

  const inputClass =
    'h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-foreground/40';
  const labelClass = 'block text-xs font-semibold text-foreground/60 mb-1';

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-white/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-lg font-bold text-foreground">Settings</h1>
      </header>
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {/* Profile */}
        <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/20 text-lg font-bold text-primary">
              {user?.name?.charAt(0).toUpperCase() || 'U'}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-foreground">
                {user?.name || 'Volunteer'}
              </p>
              <p className="truncate text-xs text-foreground/50">{user?.email}</p>
              <span className="mt-1 inline-block rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-bold uppercase text-primary">
                {user?.role}
              </span>
            </div>
          </div>
        </div>

        {/* Appearance */}
        <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-bold text-foreground">Dark Mode</p>
              <p className="text-xs text-foreground/50">{darkMode ? 'Dark' : 'Light'} theme</p>
            </div>
            <button
              onClick={toggleDarkMode}
              role="switch"
              aria-checked={darkMode}
              aria-label="Toggle dark mode"
              className={`relative h-7 w-12 rounded-full transition-colors ${darkMode ? 'bg-primary' : 'bg-foreground/20'}`}
            >
              <span
                aria-hidden="true"
                className="absolute top-0.5 h-6 w-6 rounded-full bg-card shadow transition-transform"
                style={{ transform: darkMode ? 'translateX(20px)' : 'translateX(2px)' }}
              />
            </button>
          </div>
        </div>

        {/* Safe Mode */}
        <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${safeMode ? 'bg-red-100' : 'bg-green-100'}`}>
                <Power size={18} className={safeMode ? 'text-red-500' : 'text-green-600'} />
              </div>
              <div>
                <p className="text-sm font-bold text-foreground">Safe Mode</p>
                <p className="text-xs text-foreground/50">
                  {safeMode ? 'Recognition paused' : 'System active'}
                </p>
              </div>
            </div>
            <button
              onClick={toggleSafeMode}
              className={`relative h-7 w-12 rounded-full transition-colors ${safeMode ? 'bg-red-500' : 'bg-green-500'}`}
            >
              <span
                className={`absolute top-0.5 h-6 w-6 rounded-full bg-card shadow transition-transform ${safeMode ? 'left-5.5' : 'left-0.5'}`}
                style={{ transform: safeMode ? 'translateX(20px)' : 'translateX(0)' }}
              />
            </button>
          </div>
          {safeMode && (
            <div className="mt-3 flex items-center gap-2 rounded-xl bg-amber-50 p-2 text-xs font-medium text-amber-700">
              <AlertTriangle size={14} />
              <span>All recognition and auto-logging is paused.</span>
            </div>
          )}
        </div>

        {/* Cameras */}
        <div className="mb-4 rounded-2xl border border-border bg-card shadow-sm">
          <div className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <Camera size={18} className="text-foreground/50" />
              <span className="text-sm font-bold text-foreground">Cameras</span>
            </div>
            <button
              onClick={() => setShowCameraForm(!showCameraForm)}
              className="rounded-xl bg-primary px-3 py-1 text-xs font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98]"
            >
              {showCameraForm ? 'Cancel' : 'Add'}
            </button>
          </div>

          {showCameraForm && (
            <div className="space-y-2 border-t border-border p-4">
              <input
                type="text"
                placeholder="Camera name"
                value={cameraForm.name}
                onChange={(e) => setCameraForm({ ...cameraForm, name: e.target.value })}
                className={inputClass}
              />
              <input
                type="text"
                placeholder="RTSP URL"
                value={cameraForm.rtsp_url}
                onChange={(e) => setCameraForm({ ...cameraForm, rtsp_url: e.target.value })}
                className={inputClass}
              />
              <input
                type="text"
                placeholder="Zone label (optional)"
                value={cameraForm.zone_label}
                onChange={(e) => setCameraForm({ ...cameraForm, zone_label: e.target.value })}
                className={inputClass}
              />
              <div className="flex gap-2">
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={cameraForm.fps}
                  onChange={(e) => setCameraForm({ ...cameraForm, fps: parseInt(e.target.value) || 1 })}
                  className="h-10 w-20 rounded-xl border border-border bg-background px-3 text-sm text-foreground"
                />
                <label className="flex items-center gap-2 text-sm text-foreground/50">
                  <input
                    type="checkbox"
                    checked={cameraForm.enable_health_check}
                    onChange={(e) => setCameraForm({ ...cameraForm, enable_health_check: e.target.checked })}
                  />
                  Health check
                </label>
              </div>
              <button
                onClick={addCamera}
                className="h-10 w-full rounded-xl bg-primary text-sm font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98]"
              >
                Save Camera
              </button>
            </div>
          )}

          {cameras.map((cam) => (
            <div key={cam.id} className="flex items-center justify-between border-t border-border p-4">
              <div>
                <p className="text-sm font-semibold text-foreground">{cam.name}</p>
                <p className="text-xs text-foreground/50">{cam.rtsp_url}</p>
                <span className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${
                  cam.status === 'streaming' ? 'bg-green-100 text-green-600' :
                  cam.status === 'reconnecting' ? 'bg-amber-100 text-amber-700' :
                  'bg-red-100 text-red-600'
                }`}>
                  {cam.status}
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => openPreview(cam.id, cam.name)}
                  className="rounded-xl bg-background px-2 py-1 text-xs font-medium text-foreground/60 border border-border transition-all hover:bg-primary/20"
                  aria-label={`Preview ${cam.name}`}
                >
                  Preview
                </button>
                <button
                  onClick={() => reconnectCamera(cam.id)}
                  className="rounded-xl bg-background px-2 py-1 text-xs font-medium text-foreground/60 border border-border transition-all hover:bg-primary/20"
                >
                  Reconnect
                </button>
                <button
                  onClick={() => setConfirmDelete(cam.id)}
                  className="rounded-xl bg-red-50 px-2 py-1 text-xs font-medium text-red-600 border border-red-200 transition-all hover:bg-red-100"
                  aria-label={`Delete ${cam.name}`}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Admin sections */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
            Admin
          </h2>
          <div className="rounded-2xl border border-border bg-card shadow-sm">
            <button
              onClick={() => navigate('/settings/users')}
              className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
            >
              <Shield size={18} className="text-foreground/50" aria-hidden="true" />
              <span className="text-sm text-foreground">Manage Users</span>
            </button>
            <div className="mx-4 h-px bg-border" />
            <button
              onClick={() => navigate('/settings/attendance')}
              className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
            >
              <Users size={18} className="text-foreground/50" aria-hidden="true" />
              <span className="text-sm text-foreground">Attendance</span>
            </button>
            <div className="mx-4 h-px bg-border" />
            <button
              onClick={() => navigate('/dashboard')}
              className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
            >
              <Database size={18} className="text-foreground/50" aria-hidden="true" />
              <span className="text-sm text-foreground">Analytics Dashboard</span>
            </button>
          </div>

          {/* Face Upload */}
          <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
            <div className="flex items-center gap-3 mb-3">
              <Upload size={18} className="text-foreground/50" />
              <span className="text-sm font-bold text-foreground">Face Upload</span>
            </div>
            <p className="text-xs text-foreground/50 mb-3">
              Upload a photo to detect faces and send them to the volunteer review queue.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleFileChange}
              className="hidden"
            />
            <div className="flex items-center gap-2 mb-3">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="rounded-xl border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground transition-all hover:bg-primary/20 active:scale-[0.98]"
              >
                Choose File
              </button>
              {uploadFile && (
                <span className="text-xs text-foreground/60 truncate max-w-[180px]">
                  {uploadFile.name}
                </span>
              )}
            </div>
            <button
              onClick={handleUpload}
              disabled={!uploadFile || uploading}
              className="h-10 w-full rounded-xl bg-primary text-sm font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
            >
              {uploading ? 'Uploading...' : 'Upload & Detect Faces'}
            </button>
            {uploadError && (
              <div className="mt-3 rounded-xl bg-red-50 p-2 text-xs font-medium text-red-600">
                {uploadError}
              </div>
            )}
            {uploadResult && (
              <div className="mt-3 space-y-1 rounded-xl bg-background p-3 text-xs">
                <p className="font-semibold text-foreground">
                  {uploadResult.faces_detected} face{uploadResult.faces_detected !== 1 ? 's' : ''} detected
                </p>
                {uploadResult.quality_passed > 0 && (
                  <p className="text-foreground/70">
                    {uploadResult.quality_passed} passed quality
                    {uploadResult.auto_logged > 0 && ` → ${uploadResult.auto_logged} auto-logged`}
                    {uploadResult.tasks_created > 0 && ` → ${uploadResult.tasks_created} task${uploadResult.tasks_created !== 1 ? 's' : ''} created`}
                  </p>
                )}
                {uploadResult.quality_failed > 0 && (
                  <p className="text-foreground/70">
                    {uploadResult.quality_failed} failed quality → skipped
                  </p>
                )}
                {uploadResult.deduplicated > 0 && (
                  <p className="text-foreground/70">
                    {uploadResult.deduplicated} deduplicated
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        {/* System Settings */}
        {settings.length > 0 && (
          <div className="mb-4 space-y-1">
            <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
              System
            </h2>
            <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
              {!showSystemEdit ? (
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
                    onClick={() => setShowSystemEdit(true)}
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
                    <label htmlFor="settings-compreface-detect-key" className={labelClass}>Detection Service API Key</label>
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
                    <label htmlFor="settings-compreface-recognize-key" className={labelClass}>Recognition Service API Key</label>
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
                    onClick={runConnectionTest}
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
                        setShowSystemEdit(false);
                        setComprefaceDetectKey('');
                        setComprefaceRecognizeKey('');
                      }}
                      className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground transition-all hover:bg-background active:scale-[0.98]"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={saveSystemSettings}
                      disabled={systemSaving}
                      className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
                    >
                      {systemSaving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tunables editor */}
        {settings.length > 0 && (
          <TunablesEditor settings={settings} onSaved={fetchData} />
        )}

        {/* General */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
            General
          </h2>
          <div className="rounded-2xl border border-border bg-card shadow-sm">
            <button
              onClick={handleLogout}
              className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left text-red-500 transition-colors hover:bg-red-50 rounded-2xl"
            >
              <LogOut size={18} aria-hidden="true" />
              <span className="text-sm font-semibold">Log Out</span>
            </button>
          </div>
        </div>
      </main>

      {/* Camera delete confirmation */}
      {confirmDelete !== null && (
        <ConfirmDialog
          message="Delete this camera? The stream will stop and its settings will be permanently removed."
          confirmLabel="Delete Camera"
          destructive
          onConfirm={() => deleteCamera(confirmDelete)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}

      {/* Camera preview modal */}
      {previewCamera && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Preview: ${previewCamera.name}`}
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/60"
          onClick={() => { setPreviewCamera(null); setPreviewSrc(null); }}
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-border bg-card p-4 shadow-xl mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-bold text-foreground">{previewCamera.name}</h2>
              <button
                onClick={() => { setPreviewCamera(null); setPreviewSrc(null); }}
                aria-label="Close preview"
                className="text-foreground/40 hover:text-foreground"
              >
                ✕
              </button>
            </div>
            {previewLoading ? (
              <div className="flex h-48 items-center justify-center text-sm text-foreground/50">Capturing frame…</div>
            ) : previewSrc ? (
              <img src={previewSrc} alt={`Preview from ${previewCamera.name}`} className="w-full rounded-xl object-cover" />
            ) : (
              <div className="flex h-48 items-center justify-center text-sm text-foreground/50">No frame available</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
