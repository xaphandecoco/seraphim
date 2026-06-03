import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/services/api';
import { buildPostgresUrl, parsePostgresUrl, buildRedisUrl, parseRedisUrl } from '@/services/connectionUrl';
import {
  Lock,
  Database,
  Server,
  Camera,
  Check,
  Plug,
  SkipForward,
  ChevronRight,
  ChevronLeft,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Eye,
  EyeOff,
} from 'lucide-react';

export function SetupPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const defaultDb = parsePostgresUrl('');
  const defaultRedis = parseRedisUrl('');

  const [form, setForm] = useState({
    database_url: '',
    redis_url: '',
    compreface_url: '',
    compreface_detect_api_key: '',
    compreface_recognize_api_key: '',
    civicrm_url: '',
    civicrm_api_key: '',
    civicrm_site_key: '',
    admin_email: '',
    admin_password: '',
    admin_password_confirm: '',
    admin_name: '',
    camera_name: '',
    camera_rtsp: '',
  });

  const [dbFields, setDbFields] = useState(defaultDb);
  const [redisFields, setRedisFields] = useState(defaultRedis);

  const [showDbPassword, setShowDbPassword] = useState(false);
  const [showRedisPassword, setShowRedisPassword] = useState(false);
  const [showAdminPassword, setShowAdminPassword] = useState(false);
  const [showAdminPasswordConfirm, setShowAdminPasswordConfirm] = useState(false);
  const [showComprefaceDetectKey, setShowComprefaceDetectKey] = useState(false);
  const [showComprefaceRecognizeKey, setShowComprefaceRecognizeKey] = useState(false);

  const [testResult, setTestResult] = useState<{
    database_ok?: boolean;
    database_message?: string;
    redis_ok?: boolean;
    redis_message?: string;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  const [serviceTestResult, setServiceTestResult] = useState<{
    compreface_ok?: boolean;
    compreface_message?: string;
    civicrm_ok?: boolean;
    civicrm_message?: string;
  } | null>(null);
  const [serviceTesting, setServiceTesting] = useState(false);

  const [skippedSteps, setSkippedSteps] = useState<Set<number>>(new Set());

  useEffect(() => {
    api.get('/setup/status')
      .then(res => {
        if (res.data.setup_complete) {
          navigate('/login');
        }
      })
      .catch(() => {});
  }, [navigate]);

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

  const runServiceTest = async () => {
    setServiceTesting(true);
    setServiceTestResult(null);
    try {
      const res = await api.post('/setup/test-services', {
        compreface_url: form.compreface_url || undefined,
        compreface_detect_api_key: form.compreface_detect_api_key || undefined,
        compreface_recognize_api_key: form.compreface_recognize_api_key || undefined,
        civicrm_url: form.civicrm_url || undefined,
      });
      setServiceTestResult(res.data);
    } catch (err: any) {
      setServiceTestResult({
        compreface_ok: false,
        compreface_message: err.response?.data?.detail || 'Test request failed',
        civicrm_ok: false,
        civicrm_message: '',
      });
    } finally {
      setServiceTesting(false);
    }
  };

  const handleSkip = (stepNum: number) => {
    setSkippedSteps(prev => new Set(prev).add(stepNum));
    setStep(stepNum + 1);
  };

  const handleSubmit = async () => {
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

    setLoading(true);
    setError('');

    try {
      await api.post('/setup', {
        database_url,
        redis_url,
        compreface_url: form.compreface_url,
        compreface_detect_api_key: form.compreface_detect_api_key || undefined,
        compreface_recognize_api_key: form.compreface_recognize_api_key || undefined,
        civicrm_url: form.civicrm_url || undefined,
        civicrm_api_key: form.civicrm_api_key || undefined,
        civicrm_site_key: form.civicrm_site_key || undefined,
        admin_email: form.admin_email,
        admin_password: form.admin_password,
        admin_name: form.admin_name,
        cameras: skippedSteps.has(4) ? [] : [{
          name: form.camera_name,
          rtsp_url: form.camera_rtsp,
        }],
      });
      navigate('/login');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Setup failed');
    } finally {
      setLoading(false);
    }
  };

  const steps = [
    { num: 1, label: 'Database', icon: Database },
    { num: 2, label: 'Services', icon: Server },
    { num: 3, label: 'Admin', icon: Lock },
    { num: 4, label: 'Camera', icon: Camera },
    { num: 5, label: 'Confirm', icon: Check },
  ];

  const inputClass =
    'h-12 w-full rounded-xl border border-border bg-background px-4 text-sm text-foreground transition-colors focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-foreground/40';
  const labelClass = 'block text-xs font-semibold text-foreground/60 mb-1.5';

  const isOptionalStep = (s: number) => s === 2 || s === 4;

  const passwordChecks = [
    { label: 'At least 12 characters', valid: form.admin_password.length >= 12 },
    { label: 'One uppercase letter', valid: /[A-Z]/.test(form.admin_password) },
    { label: 'One lowercase letter', valid: /[a-z]/.test(form.admin_password) },
    { label: 'One number', valid: /[0-9]/.test(form.admin_password) },
    { label: 'One special character', valid: /[!@#$%^&*()_+\-=\[\]{}|;':",./<>?]/.test(form.admin_password) },
  ];

  const passwordsMatch = form.admin_password_confirm === '' || form.admin_password === form.admin_password_confirm;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-primary via-[#F9E79F] to-[#FBF8F0] px-4 py-8">
      <div className="w-full max-w-lg">
        {/* Header */}
        <div className="mb-6 text-center">
          <img
            src="/logo.png"
            alt="LNC Logo"
            className="mx-auto mb-3 h-16 w-auto drop-shadow-sm"
          />
          <h1 className="text-2xl font-extrabold text-foreground">LNC Attendance</h1>
          <p className="mt-1 text-sm font-medium text-foreground/70">Initial Setup</p>
        </div>

        {/* Step indicator */}
        <div className="mb-6 rounded-2xl border border-border bg-white/90 p-4 shadow-lg shadow-primary/10 backdrop-blur-sm">
          <div className="flex items-center justify-between">
            {steps.map((s, idx) => {
              const Icon = s.icon;
              const isActive = step >= s.num;
              const isCurrent = step === s.num;
              const isSkipped = skippedSteps.has(s.num);
              return (
                <div key={s.num} className="flex flex-1 items-center">
                  <div className="flex flex-col items-center">
                    <div
                      className={`flex h-10 w-10 items-center justify-center rounded-full text-sm font-bold transition-all ${
                        isCurrent
                          ? 'bg-primary text-foreground ring-2 ring-primary ring-offset-2 ring-offset-white'
                          : isActive
                          ? 'bg-primary text-foreground'
                          : 'bg-background text-foreground/40'
                      }`}
                    >
                      <Icon size={16} />
                    </div>
                    <span className={`mt-2 text-[11px] font-semibold ${isCurrent ? 'text-primary' : 'text-foreground/50'}`}>
                      {s.label}
                    </span>
                    <span className="h-4 text-[10px]">
                      {isSkipped ? (
                        <span className="font-semibold text-amber-600">Skipped</span>
                      ) : (
                        <span className="invisible">Skipped</span>
                      )}
                    </span>
                  </div>
                  {idx < steps.length - 1 && (
                    <div className="mx-1 mb-6 flex-1">
                      <div
                        className={`h-0.5 transition-colors ${
                          step > s.num ? 'bg-primary' : 'bg-border'
                        }`}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm font-medium text-red-600">
            <AlertCircle size={16} className="shrink-0" />
            {error}
          </div>
        )}

        {/* Form content */}
        <div className="space-y-4">
          {step === 1 && (
            <>
              <div className="flex items-center gap-2">
                <Database size={18} className="text-primary" />
                <h2 className="text-lg font-bold text-foreground">Database & Cache</h2>
              </div>

              {/* PostgreSQL Section */}
              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <p className="mb-3 text-sm font-bold text-foreground">PostgreSQL</p>
                <div className="space-y-3">
                  <div>
                    <label className={labelClass}>Host / URL</label>
                    <input
                      type="text"
                      value={dbFields.host}
                      onChange={(e) => setDbFields({ ...dbFields, host: e.target.value })}
                      placeholder="e.g. postgres or localhost"
                      className={inputClass}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={labelClass}>Port</label>
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
                  <div className="relative">
                    <label className={labelClass}>Password</label>
                    <input
                      type={showDbPassword ? 'text' : 'password'}
                      value={dbFields.password}
                      onChange={(e) => setDbFields({ ...dbFields, password: e.target.value })}
                      placeholder="Database password"
                      className={`${inputClass} pr-10`}
                    />
                    <button
                      type="button"
                      aria-label={showDbPassword ? 'Hide Database Password' : 'Show Database Password'}
                      onClick={() => setShowDbPassword(!showDbPassword)}
                      className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                    >
                      {showDbPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
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
                </div>
              </div>

              {/* Redis Section */}
              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <p className="mb-3 text-sm font-bold text-foreground">Redis Cache</p>
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={labelClass}>Host</label>
                      <input
                        type="text"
                        value={redisFields.host}
                        onChange={(e) => setRedisFields({ ...redisFields, host: e.target.value })}
                        placeholder="e.g. redis or localhost"
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label className={labelClass}>Port</label>
                      <input
                        type="text"
                        value={redisFields.port}
                        onChange={(e) => setRedisFields({ ...redisFields, port: e.target.value })}
                        placeholder="6379"
                        className={inputClass}
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={labelClass}>DB Index</label>
                      <input
                        type="text"
                        value={redisFields.db}
                        onChange={(e) => setRedisFields({ ...redisFields, db: e.target.value })}
                        placeholder="0"
                        className={inputClass}
                      />
                    </div>
                    <div className="relative">
                      <label className={labelClass}>Password (optional)</label>
                      <input
                        type={showRedisPassword ? 'text' : 'password'}
                        value={redisFields.password}
                        onChange={(e) => setRedisFields({ ...redisFields, password: e.target.value })}
                        placeholder="No auth"
                        className={`${inputClass} pr-10`}
                      />
                      <button
                        type="button"
                        aria-label={showRedisPassword ? 'Hide password' : 'Show password'}
                        onClick={() => setShowRedisPassword(!showRedisPassword)}
                        className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                      >
                        {showRedisPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* Test Connections */}
              <button
                onClick={runConnectionTest}
                disabled={testing}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-border bg-white/90 text-sm font-semibold text-foreground shadow-sm transition-all hover:bg-background active:scale-[0.98] disabled:opacity-50"
              >
                <Plug size={16} />
                {testing ? 'Testing...' : 'Test Connections'}
              </button>

              {testResult && (
                <div className="space-y-2 rounded-2xl border border-border bg-white/90 p-4 text-sm shadow-sm backdrop-blur-sm">
                  <div className="flex items-center gap-3">
                    {testResult.database_ok ? (
                      <CheckCircle2 size={18} className="shrink-0 text-green-600" />
                    ) : (
                      <XCircle size={18} className="shrink-0 text-red-500" />
                    )}
                    <div className="min-w-0">
                      <span className="font-semibold text-foreground">PostgreSQL</span>
                      <p className="text-foreground/60">{testResult.database_message}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {testResult.redis_ok ? (
                      <CheckCircle2 size={18} className="shrink-0 text-green-600" />
                    ) : (
                      <XCircle size={18} className="shrink-0 text-red-500" />
                    )}
                    <div className="min-w-0">
                      <span className="font-semibold text-foreground">Redis</span>
                      <p className="text-foreground/60">{testResult.redis_message}</p>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {step === 2 && (
            <>
              <div className="flex items-center gap-2">
                <Server size={18} className="text-primary" />
                <h2 className="text-lg font-bold text-foreground">External Services</h2>
              </div>

              {/* Compreface */}
              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <p className="mb-1 text-sm font-bold text-foreground">CompreFace</p>
                <p className="mb-3 text-xs text-foreground/50">
                  CompreFace issues one API key per service. Paste the key from each service's application page in the CompreFace dashboard.
                </p>
                <div className="space-y-3">
                  <div>
                    <label htmlFor="compreface-url" className={labelClass}>Service URL</label>
                    <input
                      id="compreface-url"
                      type="text"
                      value={form.compreface_url}
                      onChange={(e) => setForm({ ...form, compreface_url: e.target.value })}
                      placeholder="http://compreface-api:8080"
                      className={inputClass}
                      autoComplete="off"
                    />
                  </div>
                  <div className="relative">
                    <label htmlFor="compreface-detect-key" className={labelClass}>Detection Service API Key</label>
                    <input
                      id="compreface-detect-key"
                      type={showComprefaceDetectKey ? 'text' : 'password'}
                      value={form.compreface_detect_api_key}
                      onChange={(e) => setForm({ ...form, compreface_detect_api_key: e.target.value })}
                      placeholder="Detection service UUID key"
                      className={`${inputClass} pr-10`}
                      autoComplete="off"
                    />
                    <button
                      type="button"
                      aria-label={showComprefaceDetectKey ? 'Hide Detection Service API Key' : 'Show Detection Service API Key'}
                      onClick={() => setShowComprefaceDetectKey(!showComprefaceDetectKey)}
                      className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                    >
                      {showComprefaceDetectKey ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
                  </div>
                  <div className="relative">
                    <label htmlFor="compreface-recognize-key" className={labelClass}>Recognition Service API Key</label>
                    <input
                      id="compreface-recognize-key"
                      type={showComprefaceRecognizeKey ? 'text' : 'password'}
                      value={form.compreface_recognize_api_key}
                      onChange={(e) => setForm({ ...form, compreface_recognize_api_key: e.target.value })}
                      placeholder="Recognition service UUID key"
                      className={`${inputClass} pr-10`}
                      autoComplete="off"
                    />
                    <button
                      type="button"
                      aria-label={showComprefaceRecognizeKey ? 'Hide Recognition Service API Key' : 'Show Recognition Service API Key'}
                      onClick={() => setShowComprefaceRecognizeKey(!showComprefaceRecognizeKey)}
                      className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                    >
                      {showComprefaceRecognizeKey ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
                  </div>
                </div>
              </div>

              {/* CiviCRM */}
              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <p className="mb-3 text-sm font-bold text-foreground">CiviCRM Integration</p>
                <p className="mb-3 text-xs text-foreground/50">Required if configuring CiviCRM</p>
                <div className="space-y-3">
                  <div>
                    <label className={labelClass}>Service URL</label>
                    <input
                      type="text"
                      value={form.civicrm_url}
                      onChange={(e) => setForm({ ...form, civicrm_url: e.target.value })}
                      placeholder="https://crm.example.org"
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className={labelClass}>API Key</label>
                    <input
                      type="text"
                      value={form.civicrm_api_key}
                      onChange={(e) => setForm({ ...form, civicrm_api_key: e.target.value })}
                      placeholder="CiviCRM API Key"
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className={labelClass}>Site Key</label>
                    <input
                      type="text"
                      value={form.civicrm_site_key}
                      onChange={(e) => setForm({ ...form, civicrm_site_key: e.target.value })}
                      placeholder="CiviCRM Site Key"
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              <button
                onClick={runServiceTest}
                disabled={serviceTesting}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-border bg-white/90 text-sm font-semibold text-foreground shadow-sm transition-all hover:bg-background active:scale-[0.98] disabled:opacity-50"
              >
                <Plug size={16} />
                {serviceTesting ? 'Testing...' : 'Test Services'}
              </button>

              {serviceTestResult && (
                <div className="space-y-2 rounded-2xl border border-border bg-white/90 p-4 text-sm shadow-sm backdrop-blur-sm">
                  <div className="flex items-center gap-3">
                    {serviceTestResult.compreface_ok ? (
                      <CheckCircle2 size={18} className="shrink-0 text-green-600" />
                    ) : (
                      <XCircle size={18} className="shrink-0 text-red-500" />
                    )}
                    <div className="min-w-0">
                      <span className="font-semibold text-foreground">Compreface</span>
                      <p className="text-foreground/60">{serviceTestResult.compreface_message}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {serviceTestResult.civicrm_ok ? (
                      <CheckCircle2 size={18} className="shrink-0 text-green-600" />
                    ) : serviceTestResult.civicrm_ok === false ? (
                      <XCircle size={18} className="shrink-0 text-red-500" />
                    ) : (
                      <span className="h-[18px] w-[18px] shrink-0 rounded-full bg-gray-400" />
                    )}
                    <div className="min-w-0">
                      <span className="font-semibold text-foreground">CiviCRM</span>
                      <p className="text-foreground/60">{serviceTestResult.civicrm_message}</p>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="flex items-center gap-2">
                <Lock size={18} className="text-primary" />
                <h2 className="text-lg font-bold text-foreground">Admin Account</h2>
              </div>

              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <div className="space-y-3">
                  <div>
                    <label className={labelClass}>Email</label>
                    <input
                      type="email"
                      value={form.admin_email}
                      onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                      placeholder="admin@lightnc.org"
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className={labelClass}>Full Name</label>
                    <input
                      type="text"
                      value={form.admin_name}
                      onChange={(e) => setForm({ ...form, admin_name: e.target.value })}
                      placeholder="Admin"
                      className={inputClass}
                    />
                  </div>
                  <div className="relative">
                    <label className={labelClass}>Password</label>
                    <input
                      type={showAdminPassword ? 'text' : 'password'}
                      value={form.admin_password}
                      onChange={(e) => setForm({ ...form, admin_password: e.target.value })}
                      placeholder="Min 12 chars with A-Z, a-z, 0-9, special"
                      className={`${inputClass} pr-10`}
                    />
                    <button
                      type="button"
                      aria-label={showAdminPassword ? 'Hide Admin Password' : 'Show Admin Password'}
                      onClick={() => setShowAdminPassword(!showAdminPassword)}
                      className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                    >
                      {showAdminPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
                  </div>
                  <div className="relative">
                    <label className={labelClass}>Confirm Password</label>
                    <input
                      type={showAdminPasswordConfirm ? 'text' : 'password'}
                      value={form.admin_password_confirm}
                      onChange={(e) => setForm({ ...form, admin_password_confirm: e.target.value })}
                      placeholder="Re-enter password"
                      className={`${inputClass} pr-10`}
                    />
                    <button
                      type="button"
                      aria-label={showAdminPasswordConfirm ? 'Hide Confirm Password' : 'Show Confirm Password'}
                      onClick={() => setShowAdminPasswordConfirm(!showAdminPasswordConfirm)}
                      className="absolute right-3 top-[26px] text-foreground/40 hover:text-foreground"
                    >
                      {showAdminPasswordConfirm ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
                  </div>

                  {/* Password match error */}
                  {!passwordsMatch && (
                    <div className="flex items-center gap-2 text-xs font-medium text-red-500">
                      <AlertCircle size={14} />
                      Passwords do not match
                    </div>
                  )}

                  {/* Password strength checklist */}
                  {form.admin_password && (
                    <div className="rounded-xl bg-background p-3">
                      <p className="mb-2 text-xs font-semibold text-foreground/60">Password requirements</p>
                      <div className="space-y-1.5">
                        {passwordChecks.map((check) => (
                          <div
                            key={check.label}
                            className={`flex items-center gap-2 text-xs transition-colors ${
                              check.valid ? 'text-green-600' : 'text-foreground/40'
                            }`}
                          >
                            {check.valid ? (
                              <CheckCircle2 size={13} className="shrink-0" />
                            ) : (
                              <span className="h-[13px] w-[13px] shrink-0 rounded-full border border-foreground/20" />
                            )}
                            {check.label}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <div className="flex items-center gap-2">
                <Camera size={18} className="text-primary" />
                <h2 className="text-lg font-bold text-foreground">Initial Camera</h2>
              </div>

              <div className="rounded-2xl border border-border bg-white/90 p-4 shadow-sm backdrop-blur-sm">
                <div className="space-y-3">
                  <div>
                    <label className={labelClass}>Camera Name</label>
                    <input
                      type="text"
                      value={form.camera_name}
                      onChange={(e) => setForm({ ...form, camera_name: e.target.value })}
                      placeholder="Entrance Camera"
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className={labelClass}>RTSP URL</label>
                    <input
                      type="text"
                      value={form.camera_rtsp}
                      onChange={(e) => setForm({ ...form, camera_rtsp: e.target.value })}
                      placeholder="rtsp://mock-camera-1:8554/cam1"
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>
            </>
          )}

          {step === 5 && (
            <>
              <div className="flex items-center gap-2">
                <Check size={18} className="text-primary" />
                <h2 className="text-lg font-bold text-foreground">Review & Confirm</h2>
              </div>

              <div className="space-y-3 rounded-2xl border border-border bg-white/90 p-4 text-sm shadow-sm backdrop-blur-sm">
                <div className="space-y-2">
                  <div>
                    <span className="font-semibold text-foreground/60">Database</span>
                    <p className="mt-0.5 break-all font-mono text-xs text-foreground">
                      {buildPostgresUrl(dbFields.host, dbFields.port, dbFields.name, dbFields.username, dbFields.password ? '****' : '', dbFields.extra)}
                    </p>
                  </div>
                  <div className="border-t border-border pt-2">
                    <span className="font-semibold text-foreground/60">Redis</span>
                    <p className="mt-0.5 break-all font-mono text-xs text-foreground">
                      {buildRedisUrl(redisFields.host, redisFields.port, redisFields.db, redisFields.password ? '****' : '')}
                    </p>
                  </div>
                  <div className="border-t border-border pt-2">
                    <span className="font-semibold text-foreground/60">Compreface</span>
                    <p className="mt-0.5 break-all font-mono text-xs text-foreground">{form.compreface_url}</p>
                  </div>
                  {skippedSteps.has(2) && (
                    <div className="flex items-center gap-2 rounded-lg bg-amber-50 p-2 text-xs font-medium text-amber-700">
                      <SkipForward size={14} />
                      External Services skipped — configure later in Settings
                    </div>
                  )}
                  <div className="border-t border-border pt-2">
                    <span className="font-semibold text-foreground/60">Admin</span>
                    <p className="mt-0.5 text-foreground">{form.admin_email}</p>
                  </div>
                  {skippedSteps.has(4) ? (
                    <div className="flex items-center gap-2 rounded-lg bg-amber-50 p-2 text-xs font-medium text-amber-700">
                      <SkipForward size={14} />
                      Camera setup skipped — configure later in Settings
                    </div>
                  ) : (
                    <div className="border-t border-border pt-2">
                      <span className="font-semibold text-foreground/60">Camera</span>
                      <p className="mt-0.5 text-foreground">{form.camera_name}</p>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Navigation buttons */}
        <div className="mt-6 flex gap-3">
          {step > 1 && (
            <button
              onClick={() => setStep(step - 1)}
              className="flex h-12 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/90 px-5 text-sm font-semibold text-foreground shadow-sm transition-all hover:bg-background active:scale-[0.98]"
            >
              <ChevronLeft size={16} />
              Back
            </button>
          )}
          <div className="flex-1" />
          {isOptionalStep(step) && (
            <button
              onClick={() => handleSkip(step)}
              className="flex h-12 items-center justify-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-5 text-sm font-semibold text-amber-700 transition-all hover:bg-amber-100 active:scale-[0.98]"
            >
              <SkipForward size={16} />
              Skip
            </button>
          )}
          {step < 5 ? (
            <button
              onClick={() => setStep(step + 1)}
              disabled={
                // Block advancing from Admin step unless password is valid and confirmed
                step === 3 && (!passwordChecks.every((c) => c.valid) || !passwordsMatch || !form.admin_password_confirm)
              }
              className="flex h-12 items-center justify-center gap-1.5 rounded-xl bg-primary px-6 text-sm font-bold text-primary-foreground shadow-md transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
            >
              Next
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={loading}
              className="flex h-12 items-center justify-center gap-1.5 rounded-xl bg-primary px-6 text-sm font-bold text-foreground shadow-md shadow-primary/30 transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
            >
              {loading ? 'Setting up...' : 'Complete Setup'}
              <Check size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
