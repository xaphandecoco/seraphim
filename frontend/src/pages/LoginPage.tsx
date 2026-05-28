import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Chrome, Lock, Mail, ArrowRight } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';

export function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

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

      // Fetch user info
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
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-[#F5D547] via-[#F9E79F] to-[#FBF8F0] px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img
            src="/logo.png"
            alt="LNC Logo"
            className="mx-auto mb-4 h-24 w-auto drop-shadow-sm"
          />
          <h1 className="text-3xl font-extrabold tracking-tight text-[#1F2128]">
            LNC Attendance
          </h1>
          <p className="mt-1 text-sm font-medium text-[#1F2128]/70">
            Volunteer Portal
          </p>
        </div>

        <div className="rounded-2xl border border-[#E8DDA8] bg-white/90 p-6 shadow-lg shadow-[#F5D547]/20 backdrop-blur-sm">
          <button
            onClick={handleGoogleLogin}
            className="flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-[#E8DDA8] bg-white font-medium text-[#1F2128] shadow-sm transition-all hover:bg-[#FBF8F0] active:scale-[0.98]"
          >
            <Chrome size={18} />
            Sign in with Google
          </button>

          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-[#E8DDA8]" />
            <span className="text-xs font-semibold uppercase tracking-wider text-[#1F2128]/50">or</span>
            <div className="h-px flex-1 bg-[#E8DDA8]" />
          </div>

          <form onSubmit={handlePasswordLogin} className="space-y-3">
            <div className="relative">
              <Mail
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[#1F2128]/40"
              />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email address"
                className="h-12 w-full rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] pl-10 pr-4 text-sm text-[#1F2128] outline-none placeholder:text-[#1F2128]/40 focus:border-[#F5D547] focus:ring-2 focus:ring-[#F5D547]/30"
              />
            </div>
            <div className="relative">
              <Lock
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[#1F2128]/40"
              />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                className="h-12 w-full rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] pl-10 pr-4 text-sm text-[#1F2128] outline-none placeholder:text-[#1F2128]/40 focus:border-[#F5D547] focus:ring-2 focus:ring-[#F5D547]/30"
              />
            </div>

            {error && (
              <p className="text-center text-xs font-medium text-red-500">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#F5D547] font-bold text-[#1F2128] shadow-md shadow-[#F5D547]/30 transition-all hover:bg-[#E5C53F] active:scale-[0.98] disabled:opacity-50"
            >
              {loading ? 'Signing in...' : 'Sign in'}
              <ArrowRight size={16} />
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-[#1F2128]/50">
          Light of the World Worldwide Ministries — North Caloocan
        </p>
      </div>
    </div>
  );
}
