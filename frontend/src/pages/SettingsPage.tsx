import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, Shield, Users, Camera, Power, AlertTriangle, Database, Settings2, Plug, Upload } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import type { Camera as CameraType } from '@/types';

interface SettingItem {
  key: string;
  value: { value: any };
  category: string;
  description?: string;
  requires_restart?: boolean;
  sensitive?: boolean;
}

function buildPostgresUrl(
  host: string,
  port: string,
  name: string,
  username: string,
  password: string,
  extra: string
): string {
  let url = `postgresql+asyncpg://${encodeURIComponent(username)}`;
  if (password) {
    url += `:${encodeURIComponent(password)}`;
  }
  url += `@${host}`;
  if (port) {
    url += `:${port}`;
  }
  url += `/${name}`;
  if (extra) {
    const sep = extra.startsWith('?') ? '' : '?';
    url += `${sep}${extra}`;
  }
  return url;
}

function parsePostgresUrl(url: string): {
  host: string;
  port: string;
  name: string;
  username: string;
  password: string;
  extra: string;
} {
  const defaults = { host: '', port: '5432', name: '', username: '', password: '', extra: '' };
  try {
    const u = new URL(url);
    defaults.host = u.hostname;
    defaults.port = u.port || '5432';
    defaults.name = u.pathname.replace(/^\//, '');
    defaults.username = decodeURIComponent(u.username);
    defaults.password = decodeURIComponent(u.password);
    defaults.extra = u.search.replace(/^\?/, '');
  } catch {
    // ignore
  }
  return defaults;
}

function buildRedisUrl(
  host: string,
  port: string,
  db: string,
  password: string
): string {
  let url = 'redis://';
  if (password) {
    url += `:${encodeURIComponent(password)}@`;
  }
  url += `${host}`;
  if (port) {
    url += `:${port}`;
  }
  url += `/${db}`;
  return url;
}

function parseRedisUrl(url: string): {
  host: string;
  port: string;
  db: string;
  password: string;
} {
  const defaults = { host: '', port: '6379', db: '0', password: '' };
  try {
    const u = new URL(url);
    defaults.host = u.hostname;
    defaults.port = u.port || '6379';
    defaults.db = u.pathname.replace(/^\//, '') || '0';
    defaults.password = decodeURIComponent(u.password);
  } catch {
    // ignore
  }
  return defaults;
}

export function SettingsPage() {
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);
  const user = useAuthStore((s) => s.user);
  const [settings, setSettings] = useState<SettingItem[]>([]);
  const [cameras, setCameras] = useState<CameraType[]>([]);
  const [safeMode, setSafeMode] = useState(false);
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

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const toggleSafeMode = async () => {
    try {
      const res = await api.post(`/settings/safe-mode?enabled=${!safeMode}`);
      setSafeMode(res.data.safe_mode);
    } catch {
      alert('Failed to toggle safe mode');
    }
  };

  const addCamera = async () => {
    try {
      await api.post('/cameras', cameraForm);
      setCameraForm({ name: '', rtsp_url: '', zone_label: '', fps: 1, enable_health_check: true });
      setShowCameraForm(false);
      fetchData();
    } catch {
      alert('Failed to add camera');
    }
  };

  const deleteCamera = async (id: number) => {
    if (!confirm('Delete this camera?')) return;
    try {
      await api.delete(`/cameras/${id}`);
      fetchData();
    } catch {
      alert('Failed to delete camera');
    }
  };

  const reconnectCamera = async (id: number) => {
    try {
      await api.post(`/cameras/${id}/reconnect`);
      alert('Reconnect initiated');
    } catch {
      alert('Failed to reconnect');
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
      await api.put('/settings', {
        settings: {
          database_url,
          redis_url,
        },
      });
      setShowSystemEdit(false);
      fetchData();
    } catch {
      alert('Failed to save system settings');
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
      <div className="flex h-screen items-center justify-center text-[#1F2128]/50">
        Loading...
      </div>
    );
  }

  const inputClass =
    'h-10 w-full rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] px-3 text-sm text-[#1F2128] focus:border-[#F5D547] focus:outline-none focus:ring-2 focus:ring-[#F5D547]/30 placeholder:text-[#1F2128]/40';
  const labelClass = 'block text-xs font-semibold text-[#1F2128]/60 mb-1';

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-[#E8DDA8] bg-white/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-lg font-bold text-[#1F2128]">Settings</h1>
      </header>
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {/* Profile */}
        <div className="mb-4 rounded-2xl border border-[#E8DDA8] bg-white p-4 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#F5D547]/20 text-lg font-bold text-[#F5D547]">
              {user?.name?.charAt(0).toUpperCase() || 'U'}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-[#1F2128]">
                {user?.name || 'Volunteer'}
              </p>
              <p className="truncate text-xs text-[#1F2128]/50">{user?.email}</p>
              <span className="mt-1 inline-block rounded-full bg-[#F5D547]/10 px-2 py-0.5 text-[10px] font-bold uppercase text-[#F5D547]">
                {user?.role}
              </span>
            </div>
          </div>
        </div>

        {/* Safe Mode */}
        <div className="mb-4 rounded-2xl border border-[#E8DDA8] bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${safeMode ? 'bg-red-100' : 'bg-green-100'}`}>
                <Power size={18} className={safeMode ? 'text-red-500' : 'text-green-600'} />
              </div>
              <div>
                <p className="text-sm font-bold text-[#1F2128]">Safe Mode</p>
                <p className="text-xs text-[#1F2128]/50">
                  {safeMode ? 'Recognition paused' : 'System active'}
                </p>
              </div>
            </div>
            <button
              onClick={toggleSafeMode}
              className={`relative h-7 w-12 rounded-full transition-colors ${safeMode ? 'bg-red-500' : 'bg-green-500'}`}
            >
              <span
                className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-transform ${safeMode ? 'left-5.5' : 'left-0.5'}`}
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
        <div className="mb-4 rounded-2xl border border-[#E8DDA8] bg-white shadow-sm">
          <div className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <Camera size={18} className="text-[#1F2128]/50" />
              <span className="text-sm font-bold text-[#1F2128]">Cameras</span>
            </div>
            <button
              onClick={() => setShowCameraForm(!showCameraForm)}
              className="rounded-xl bg-[#F5D547] px-3 py-1 text-xs font-bold text-[#1F2128] shadow-sm transition-all hover:bg-[#E5C53F] active:scale-[0.98]"
            >
              {showCameraForm ? 'Cancel' : 'Add'}
            </button>
          </div>

          {showCameraForm && (
            <div className="space-y-2 border-t border-[#E8DDA8] p-4">
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
                  className="h-10 w-20 rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] px-3 text-sm text-[#1F2128]"
                />
                <label className="flex items-center gap-2 text-sm text-[#1F2128]/50">
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
                className="h-10 w-full rounded-xl bg-[#F5D547] text-sm font-bold text-[#1F2128] shadow-sm transition-all hover:bg-[#E5C53F] active:scale-[0.98]"
              >
                Save Camera
              </button>
            </div>
          )}

          {cameras.map((cam) => (
            <div key={cam.id} className="flex items-center justify-between border-t border-[#E8DDA8] p-4">
              <div>
                <p className="text-sm font-semibold text-[#1F2128]">{cam.name}</p>
                <p className="text-xs text-[#1F2128]/50">{cam.rtsp_url}</p>
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
                  onClick={() => reconnectCamera(cam.id)}
                  className="rounded-xl bg-[#FBF8F0] px-2 py-1 text-xs font-medium text-[#1F2128]/60 border border-[#E8DDA8] transition-all hover:bg-[#F5D547]/20"
                >
                  Reconnect
                </button>
                <button
                  onClick={() => deleteCamera(cam.id)}
                  className="rounded-xl bg-red-50 px-2 py-1 text-xs font-medium text-red-600 border border-red-200 transition-all hover:bg-red-100"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Admin sections */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-[#1F2128]/50">
            Admin
          </h2>
          <div className="rounded-2xl border border-[#E8DDA8] bg-white shadow-sm">
            <button className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-[#FBF8F0]">
              <Shield size={18} className="text-[#1F2128]/50" />
              <span className="text-sm text-[#1F2128]">Manage Users</span>
            </button>
            <div className="mx-4 h-px bg-[#E8DDA8]" />
            <button className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-[#FBF8F0]">
              <Users size={18} className="text-[#1F2128]/50" />
              <span className="text-sm text-[#1F2128]">Manage Members</span>
            </button>
          </div>

          {/* Face Upload */}
          <div className="rounded-2xl border border-[#E8DDA8] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-3 mb-3">
              <Upload size={18} className="text-[#1F2128]/50" />
              <span className="text-sm font-bold text-[#1F2128]">Face Upload</span>
            </div>
            <p className="text-xs text-[#1F2128]/50 mb-3">
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
                className="rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] px-3 py-2 text-xs font-semibold text-[#1F2128] transition-all hover:bg-[#F5D547]/20 active:scale-[0.98]"
              >
                Choose File
              </button>
              {uploadFile && (
                <span className="text-xs text-[#1F2128]/60 truncate max-w-[180px]">
                  {uploadFile.name}
                </span>
              )}
            </div>
            <button
              onClick={handleUpload}
              disabled={!uploadFile || uploading}
              className="h-10 w-full rounded-xl bg-[#F5D547] text-sm font-bold text-[#1F2128] shadow-sm transition-all hover:bg-[#E5C53F] active:scale-[0.98] disabled:opacity-50"
            >
              {uploading ? 'Uploading...' : 'Upload & Detect Faces'}
            </button>
            {uploadError && (
              <div className="mt-3 rounded-xl bg-red-50 p-2 text-xs font-medium text-red-600">
                {uploadError}
              </div>
            )}
            {uploadResult && (
              <div className="mt-3 space-y-1 rounded-xl bg-[#FBF8F0] p-3 text-xs">
                <p className="font-semibold text-[#1F2128]">
                  {uploadResult.faces_detected} face{uploadResult.faces_detected !== 1 ? 's' : ''} detected
                </p>
                {uploadResult.quality_passed > 0 && (
                  <p className="text-[#1F2128]/70">
                    {uploadResult.quality_passed} passed quality
                    {uploadResult.auto_logged > 0 && ` → ${uploadResult.auto_logged} auto-logged`}
                    {uploadResult.tasks_created > 0 && ` → ${uploadResult.tasks_created} task${uploadResult.tasks_created !== 1 ? 's' : ''} created`}
                  </p>
                )}
                {uploadResult.quality_failed > 0 && (
                  <p className="text-[#1F2128]/70">
                    {uploadResult.quality_failed} failed quality → skipped
                  </p>
                )}
                {uploadResult.deduplicated > 0 && (
                  <p className="text-[#1F2128]/70">
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
            <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-[#1F2128]/50">
              System
            </h2>
            <div className="rounded-2xl border border-[#E8DDA8] bg-white p-4 shadow-sm">
              {!showSystemEdit ? (
                <>
                  <div className="space-y-2">
                    {settings.map((s) => (
                      <div key={s.key} className="flex items-center justify-between text-sm">
                        <span className="text-[#1F2128]/50">{s.key}</span>
                        <span className="font-mono text-xs text-[#1F2128]">
                          {s.sensitive ? '********' : String(s.value?.value ?? '')}
                        </span>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => setShowSystemEdit(true)}
                    className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] py-2 text-xs font-semibold text-[#1F2128] transition-all hover:bg-[#F5D547]/20 active:scale-[0.98]"
                  >
                    <Settings2 size={14} />
                    Edit Connection Settings
                  </button>
                </>
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm font-bold text-[#1F2128]">
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

                  <div className="border-t border-[#E8DDA8] pt-3">
                    <div className="flex items-center gap-2 text-sm font-bold text-[#1F2128]">
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

                  <button
                    onClick={runConnectionTest}
                    disabled={testing}
                    className="flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-[#E8DDA8] bg-white text-xs font-semibold text-[#1F2128] transition-all hover:bg-[#FBF8F0] active:scale-[0.98] disabled:opacity-50"
                  >
                    <Plug size={14} />
                    {testing ? 'Testing...' : 'Test Connections'}
                  </button>

                  {testResult && (
                    <div className="space-y-2 rounded-xl bg-[#FBF8F0] p-3 text-xs">
                      <div className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${testResult.database_ok ? 'bg-green-500' : 'bg-red-500'}`} />
                        <span className="font-semibold text-[#1F2128]">Postgres</span>
                        <span className="text-[#1F2128]/50">{testResult.database_message}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${testResult.redis_ok ? 'bg-green-500' : 'bg-red-500'}`} />
                        <span className="font-semibold text-[#1F2128]">Redis</span>
                        <span className="text-[#1F2128]/50">{testResult.redis_message}</span>
                      </div>
                    </div>
                  )}

                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={() => setShowSystemEdit(false)}
                      className="flex h-10 flex-1 items-center justify-center rounded-xl border border-[#E8DDA8] bg-white text-xs font-semibold text-[#1F2128] transition-all hover:bg-[#FBF8F0] active:scale-[0.98]"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={saveSystemSettings}
                      disabled={systemSaving}
                      className="flex h-10 flex-1 items-center justify-center rounded-xl bg-[#F5D547] text-xs font-bold text-[#1F2128] shadow-sm transition-all hover:bg-[#E5C53F] active:scale-[0.98] disabled:opacity-50"
                    >
                      {systemSaving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* General */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-[#1F2128]/50">
            General
          </h2>
          <div className="rounded-2xl border border-[#E8DDA8] bg-white shadow-sm">
            <button
              onClick={handleLogout}
              className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left text-red-500 transition-colors hover:bg-red-50 rounded-2xl"
            >
              <LogOut size={18} />
              <span className="text-sm font-semibold">Log Out</span>
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
