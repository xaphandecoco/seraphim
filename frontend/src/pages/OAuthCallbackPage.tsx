import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';

export function OAuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const login = useAuthStore((s) => s.login);

  useEffect(() => {
    const token = searchParams.get('token');

    if (token) {
      // Backend redirected with the access token directly
      api
        .get('/auth/me', { headers: { Authorization: `Bearer ${token}` } })
        .then((res) => {
          login(res.data, token);
          navigate('/', { replace: true });
        })
        .catch(() => {
          navigate('/login', { replace: true });
        });
    } else {
      navigate('/login', { replace: true });
    }
  }, [searchParams, login, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary via-[#F9E79F] to-[#FBF8F0]">
      <div className="text-center" aria-live="polite">
        <div
          className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-[#1F2128]"
          role="status"
          aria-label="Signing you in"
        />
        <p className="text-sm font-medium text-foreground/70">Signing you in…</p>
      </div>
    </div>
  );
}
