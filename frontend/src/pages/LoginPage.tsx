import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Chrome, Lock, Mail, ArrowRight } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';

export function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const token = useAuthStore((s) => s.token);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [googleEnabled, setGoogleEnabled] = useState(false);

  // Redirect if already authenticated
  useEffect(() => {
    if (token) navigate('/', { replace: true });
  }, [token, navigate]);

  useEffect(() => {
    api.get<{ google_oauth_enabled: boolean }>('/auth/config')
      .then((res) => setGoogleEnabled(res.data.google_oauth_enabled))
      .catch(() => {});
  }, []);

  const handleGoogleLogin = () => {
    window.location.href = '/api/auth/google';
  };

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await api.post('/auth/login', { email, password });
      const { access_token } = res.data;

      const meRes = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${access_token}` }
      });

      login(meRes.data, access_token);
      navigate('/');
    } catch {
      setError('Invalid email or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-primary via-[#F9E79F] to-[#FBF8F0] px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img
            src="/logo.png"
            alt="LNC Logo"
            className="mx-auto mb-4 h-24 w-auto drop-shadow-sm"
          />
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">
            LNC Attendance
          </h1>
          <p className="mt-1 text-sm font-medium text-foreground/70">
            Volunteer Portal
          </p>
        </div>

        <div className="rounded-2xl border border-border bg-white/90 p-6 shadow-lg shadow-primary/20 backdrop-blur-sm">
          {googleEnabled && (
            <>
              <button
                onClick={handleGoogleLogin}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-border bg-card font-medium text-foreground shadow-sm transition-all hover:bg-background active:scale-[0.98]"
                aria-label="Sign in with Google"
              >
                <Chrome size={18} aria-hidden="true" />
                Sign in with Google
              </button>

              <div className="my-5 flex items-center gap-3">
                <div className="h-px flex-1 bg-border" />
                <span className="text-xs font-semibold uppercase tracking-wider text-foreground/50">or</span>
                <div className="h-px flex-1 bg-border" />
              </div>
            </>
          )}

          <form onSubmit={handlePasswordLogin} className="space-y-3">
            <div className="relative">
              <label htmlFor="email" className="sr-only">Email address</label>
              <Mail
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40"
                aria-hidden="true"
              />
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email address"
                autoComplete="email"
                className="h-12 w-full rounded-xl border border-border bg-background pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-foreground/40 focus:border-primary focus:ring-2 focus:ring-primary/30"
              />
            </div>
            <div className="relative">
              <label htmlFor="password" className="sr-only">Password</label>
              <Lock
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40"
                aria-hidden="true"
              />
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                autoComplete="current-password"
                className="h-12 w-full rounded-xl border border-border bg-background pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-foreground/40 focus:border-primary focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {error && (
              <p role="alert" className="text-center text-xs font-medium text-red-500">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary font-bold text-foreground shadow-md shadow-primary/30 transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50"
            >
              {loading ? 'Signing in…' : 'Sign in'}
              <ArrowRight size={16} aria-hidden="true" />
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-foreground/50">
          Light of the World Worldwide Ministries — North Caloocan
        </p>
      </div>
    </div>
  );
}
