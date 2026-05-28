import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';

export function OAuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const login = useAuthStore((s) => s.login);

  useEffect(() => {
    const code = searchParams.get('code');
    const token = searchParams.get('token');

    if (token) {
      // Backend redirected with token directly
      const userParam = searchParams.get('user');
      if (userParam) {
        try {
          const user = JSON.parse(userParam);
          login(user, token);
          navigate('/', { replace: true });
          return;
        } catch {
          // fall through
        }
      }
    }

    if (code) {
      api
        .get('/auth/callback', { params: { code } })
        .then((res) => {
          const { access_token, user } = res.data;
          login(user, access_token);
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
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-[#F5D547] via-[#F9E79F] to-[#FBF8F0]">
      <div className="text-center">
        <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-3 border-[#F5D547] border-t-[#1F2128]" />
        <p className="text-sm font-medium text-[#1F2128]/70">Signing you in...</p>
      </div>
    </div>
  );
}
