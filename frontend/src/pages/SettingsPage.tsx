import { useState, useEffect, useRef } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { toast } from 'sonner';
import {
  LogOut,
  Shield,
  Users,
  Camera,
  Power,
  AlertTriangle,
  Database,
  Settings2,
  Upload,
  Activity,
  Clock,
} from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import { getFRTransitionStatus, runRemap, runConsentBackfill } from '@/services/frTransition';
import { ConnectionSettingsPanel } from '@/components/settings/ConnectionSettingsPanel';
import { TunablesPanel } from '@/components/settings/TunablesPanel';
import { ScheduledJobsPanel } from '@/components/settings/ScheduledJobsPanel';
import type { Camera as CameraType, FRTransitionStatus, SettingItem } from '@/types';

// ---------------------------------------------------------------------------
// FR Transition Status Card (T06)
// ---------------------------------------------------------------------------

type FRDialogKind = 'remap' | 'consent' | null;

function FRTransitionCard() {
  const [status, setStatus] = useState<FRTransitionStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [dialog, setDialog] = useState<FRDialogKind>(null);
  const [running, setRunning] = useState(false);

  const fetchStatus = async () => {
    setLoadingStatus(true);
    setFetchError(false);
    try {
      const data = await getFRTransitionStatus();
      setStatus(data);
    } catch {
      setFetchError(true);
    } finally {
      setLoadingStatus(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  const handleRemap = async () => {
    setDialog(null);
    setRunning(true);
    try {
      await runRemap();
      toast.success('Remap complete');
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Remap failed');
    } finally {
      setRunning(false);
    }
  };

  const handleConsentBackfill = async () => {
    setDialog(null);
    setRunning(true);
    try {
      await runConsentBackfill();
      toast.success('Consent backfill complete');
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Consent backfill failed');
    } finally {
      setRunning(false);
    }
  };

  const smokeTestTone = (s: 'pass' | 'skip' | 'fail') => {
    if (s === 'pass') return 'active' as const;
    if (s === 'skip') return 'muted' as const;
    return 'error' as const;
  };

  return (
    <>
      <div className="mb-4 space-y-1">
        <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
          FR Transition
        </h2>
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          {loadingStatus ? (
            <LoadingState message="Loading FR transition status…" />
          ) : fetchError || !status?.remap ? (
            <ErrorState
              message="Unable to load FR transition status"
              onRetry={fetchStatus}
            />
          ) : (
            <div className="space-y-4">
              {/* Remap Counts */}
              <div>
                <p className="mb-1 text-xs font-semibold text-foreground/50">Remap Counts</p>
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Total subjects</span>
                    <span className="font-mono font-semibold text-foreground">
                      {status.remap.subjects_total}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Remapped</span>
                    <span className="font-mono font-semibold text-foreground">
                      {status.remap.subjects_remapped}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Orphaned</span>
                    <span className="flex items-center gap-2">
                      <span className="font-mono font-semibold text-foreground">
                        {status.remap.subjects_orphaned}
                      </span>
                      <StatusBadge
                        label={status.remap.subjects_orphaned === 0 ? 'OK' : 'Needs attention'}
                        tone={status.remap.subjects_orphaned === 0 ? 'active' : 'warning'}
                      />
                    </span>
                  </div>
                  {status.remap.subjects_orphaned > 0 && (
                    <div className="pt-1">
                      <Link
                        to="/settings/fr-transition/orphans"
                        className="text-xs font-medium text-primary hover:underline"
                      >
                        Review orphaned subjects →
                      </Link>
                    </div>
                  )}
                </div>
              </div>

              {/* Consent Coverage */}
              <div>
                <p className="mb-1 text-xs font-semibold text-foreground/50">Consent Coverage</p>
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Active subjects</span>
                    <span className="font-mono font-semibold text-foreground">
                      {status.consent.active_subjects_total}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Consent rows</span>
                    <span className="font-mono font-semibold text-foreground">
                      {status.consent.consent_rows_created}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground/60">Missing consent</span>
                    <span className="font-mono font-semibold text-foreground">
                      {status.consent.subjects_missing_consent}
                    </span>
                  </div>
                  {status.consent.enroll_without_consent && (
                    <div className="flex items-center gap-2 pt-1">
                      <StatusBadge label="Enroll-without-consent active" tone="warning" />
                    </div>
                  )}
                </div>
              </div>

              {/* Participants */}
              <div className="flex items-center justify-between text-xs">
                <span className="text-foreground/60">Participants recorded</span>
                <span className="font-mono font-semibold text-foreground">
                  {status.participants_count}
                </span>
              </div>

              {/* Smoke Test */}
              <div>
                <p className="mb-1 text-xs font-semibold text-foreground/50">Smoke Test</p>
                <div className="flex items-center gap-2">
                  <StatusBadge
                    label={status.smoke_test.status.toUpperCase()}
                    tone={smokeTestTone(status.smoke_test.status)}
                  />
                  <span className="text-xs text-foreground/50">{status.smoke_test.detail}</span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <button
                  onClick={fetchStatus}
                  disabled={loadingStatus || running}
                  className="flex h-9 w-full items-center justify-center rounded-xl border border-border bg-background text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
                >
                  Re-run Verification
                </button>
                <div className="flex gap-2">
                  <button
                    onClick={() => setDialog('remap')}
                    disabled={running}
                    className="flex h-9 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 disabled:opacity-50 active:scale-[0.98]"
                  >
                    Run Remap
                  </button>
                  <button
                    onClick={() => setDialog('consent')}
                    disabled={running}
                    className="flex h-9 flex-1 items-center justify-center rounded-xl border border-border bg-background text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50 active:scale-[0.98]"
                  >
                    Run Consent Backfill
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {dialog === 'remap' && (
        <ConfirmDialog
          message="Re-map all CompreFace subjects to their contact records. This may take a moment."
          confirmLabel="Run Remap"
          onConfirm={handleRemap}
          onCancel={() => setDialog(null)}
        />
      )}
      {dialog === 'consent' && (
        <ConfirmDialog
          message="Backfill BiometricConsent rows for all active subjects. Existing rows will be skipped."
          confirmLabel="Run Consent Backfill"
          onConfirm={handleConsentBackfill}
          onCancel={() => setDialog(null)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

const TABS = [
  { id: 'system', label: 'System' },
  { id: 'tunables', label: 'Tunables' },
  { id: 'jobs', label: 'Jobs' },
] as const;

type TabId = (typeof TABS)[number]['id'];

// ---------------------------------------------------------------------------
// Main SettingsPage
// ---------------------------------------------------------------------------

export function SettingsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = (searchParams.get('tab') as TabId | null) ?? 'system';

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
    } catch {
      // silent on initial load
    } finally {
      setLoading(false);
    }
  }

  const handleLogout = async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // best-effort
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

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-white/95 px-4 py-3 backdrop-blur-sm dark:bg-card/95">
        <h1 className="text-lg font-bold text-foreground">Settings</h1>
      </header>

      {/* Tab navigation — horizontal scroll on mobile */}
      <div className="overflow-x-auto border-b border-border bg-card px-3 py-2">
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSearchParams({ tab: tab.id })}
              className={`whitespace-nowrap rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                activeTab === tab.id
                  ? 'bg-primary text-primary-foreground'
                  : 'text-foreground/60 hover:bg-background hover:text-foreground'
              }`}
              aria-current={activeTab === tab.id ? 'page' : undefined}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {/* ── SYSTEM TAB ──────────────────────────────────────────────── */}
        {activeTab === 'system' && (
          <>
            {/* Profile */}
            <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/20 text-lg font-bold text-primary">
                  {user?.name?.charAt(0).toUpperCase() || 'U'}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-bold text-foreground">{user?.name || 'Volunteer'}</p>
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
                  className={`relative h-7 w-12 rounded-full transition-colors ${
                    darkMode ? 'bg-primary' : 'bg-foreground/20'
                  }`}
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
                  <div
                    className={`flex h-10 w-10 items-center justify-center rounded-xl ${
                      safeMode ? 'bg-red-100' : 'bg-green-100'
                    }`}
                  >
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
                  className={`relative h-7 w-12 rounded-full transition-colors ${
                    safeMode ? 'bg-red-500' : 'bg-green-500'
                  }`}
                >
                  <span
                    className="absolute top-0.5 h-6 w-6 rounded-full bg-card shadow transition-transform"
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
                      onChange={(e) =>
                        setCameraForm({ ...cameraForm, fps: parseInt(e.target.value) || 1 })
                      }
                      className="h-10 w-20 rounded-xl border border-border bg-background px-3 text-sm text-foreground"
                    />
                    <label className="flex items-center gap-2 text-sm text-foreground/50">
                      <input
                        type="checkbox"
                        checked={cameraForm.enable_health_check}
                        onChange={(e) =>
                          setCameraForm({ ...cameraForm, enable_health_check: e.target.checked })
                        }
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
                <div
                  key={cam.id}
                  className="flex items-center justify-between border-t border-border p-4"
                >
                  <div>
                    <p className="text-sm font-semibold text-foreground">{cam.name}</p>
                    <p className="text-xs text-foreground/50">{cam.rtsp_url}</p>
                    <span
                      className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        cam.status === 'streaming'
                          ? 'bg-green-100 text-green-600'
                          : cam.status === 'reconnecting'
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-red-100 text-red-600'
                      }`}
                    >
                      {cam.status}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => openPreview(cam.id, cam.name)}
                      className="rounded-xl border border-border bg-background px-2 py-1 text-xs font-medium text-foreground/60 transition-all hover:bg-primary/20"
                      aria-label={`Preview ${cam.name}`}
                    >
                      Preview
                    </button>
                    <button
                      onClick={() => reconnectCamera(cam.id)}
                      className="rounded-xl border border-border bg-background px-2 py-1 text-xs font-medium text-foreground/60 transition-all hover:bg-primary/20"
                    >
                      Reconnect
                    </button>
                    <button
                      onClick={() => setConfirmDelete(cam.id)}
                      className="rounded-xl border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-600 transition-all hover:bg-red-100"
                      aria-label={`Delete ${cam.name}`}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* Admin section */}
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
                <div className="mx-4 h-px bg-border" />
                <button
                  onClick={() => navigate('/settings/custom-fields')}
                  className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
                >
                  <Settings2 size={18} className="text-foreground/50" aria-hidden="true" />
                  <span className="text-sm text-foreground">Custom Fields</span>
                </button>
                <div className="mx-4 h-px bg-border" />
                <button
                  onClick={() => navigate('/settings/system-status')}
                  className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
                >
                  <Activity size={18} className="text-foreground/50" aria-hidden="true" />
                  <span className="text-sm text-foreground">System Status</span>
                </button>
                <div className="mx-4 h-px bg-border" />
                <button
                  onClick={() => navigate('/settings/jobs')}
                  className="flex w-full min-h-[44px] items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-background"
                >
                  <Clock size={18} className="text-foreground/50" aria-hidden="true" />
                  <span className="text-sm text-foreground">Scheduled Jobs</span>
                </button>
              </div>
            </div>

            {/* Face Upload */}
            <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-3">
                <Upload size={18} className="text-foreground/50" />
                <span className="text-sm font-bold text-foreground">Face Upload</span>
              </div>
              <p className="mb-3 text-xs text-foreground/50">
                Upload a photo to detect faces and send them to the volunteer review queue.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                onChange={handleFileChange}
                className="hidden"
              />
              <div className="mb-3 flex items-center gap-2">
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground transition-all hover:bg-primary/20 active:scale-[0.98]"
                >
                  Choose File
                </button>
                {uploadFile && (
                  <span className="max-w-[180px] truncate text-xs text-foreground/60">
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
                    {uploadResult.faces_detected} face
                    {uploadResult.faces_detected !== 1 ? 's' : ''} detected
                  </p>
                  {uploadResult.quality_passed > 0 && (
                    <p className="text-foreground/70">
                      {uploadResult.quality_passed} passed quality
                      {uploadResult.auto_logged > 0 && ` → ${uploadResult.auto_logged} auto-logged`}
                      {uploadResult.tasks_created > 0 &&
                        ` → ${uploadResult.tasks_created} task${uploadResult.tasks_created !== 1 ? 's' : ''} created`}
                    </p>
                  )}
                  {uploadResult.quality_failed > 0 && (
                    <p className="text-foreground/70">
                      {uploadResult.quality_failed} failed quality → skipped
                    </p>
                  )}
                  {uploadResult.deduplicated > 0 && (
                    <p className="text-foreground/70">{uploadResult.deduplicated} deduplicated</p>
                  )}
                </div>
              )}
            </div>

            {/* Connection Settings */}
            {settings.length > 0 && (
              <ConnectionSettingsPanel settings={settings} onSaved={fetchData} />
            )}

            {/* FR Transition */}
            <FRTransitionCard />

            {/* General / Logout */}
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
          </>
        )}

        {/* ── TUNABLES TAB ────────────────────────────────────────────── */}
        {activeTab === 'tunables' && settings.length > 0 && (
          <TunablesPanel settings={settings} onSaved={fetchData} />
        )}

        {/* ── JOBS TAB ────────────────────────────────────────────────── */}
        {activeTab === 'jobs' && <ScheduledJobsPanel />}
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
          onClick={() => {
            setPreviewCamera(null);
            setPreviewSrc(null);
          }}
        >
          <div
            className="mx-4 w-full max-w-sm rounded-2xl border border-border bg-card p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-bold text-foreground">{previewCamera.name}</h2>
              <button
                onClick={() => {
                  setPreviewCamera(null);
                  setPreviewSrc(null);
                }}
                aria-label="Close preview"
                className="text-foreground/40 hover:text-foreground"
              >
                x
              </button>
            </div>
            {previewLoading ? (
              <div className="flex h-48 items-center justify-center text-sm text-foreground/50">
                Capturing frame…
              </div>
            ) : previewSrc ? (
              <img
                src={previewSrc}
                alt={`Preview from ${previewCamera.name}`}
                className="w-full rounded-xl object-cover"
              />
            ) : (
              <div className="flex h-48 items-center justify-center text-sm text-foreground/50">
                No frame available
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
