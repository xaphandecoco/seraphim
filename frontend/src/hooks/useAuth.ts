import { useEffect, useRef } from 'react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import type { User } from '@/types';

export function useAuth() {
  const { user, token, isAdmin, authReady, login, logout, setAuthReady } = useAuthStore();
  const initialized = useRef(false);

  // On app load: try to get a new access token from the HttpOnly refresh cookie.
  // Token stays in memory only (never localStorage).
  // Sets authReady=true when done so route guards don't flash /login prematurely.
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    if (token) {
      // Already hydrated from a same-session login — mark ready immediately.
      setAuthReady();
      return;
    }

    api.post<{ access_token: string }>('/auth/refresh')
      .then(async (res) => {
        const accessToken = res.data.access_token;
        const meRes = await api.get<User>('/auth/me', {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        login(meRes.data, accessToken);
        // authReady is set inside login()
      })
      .catch(() => {
        // No valid refresh cookie — user must log in
        setAuthReady();
      });
  }, [token, login, setAuthReady]);

  // Sync the Authorization header whenever the in-memory token changes
  useEffect(() => {
    if (token) {
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    } else {
      delete api.defaults.headers.common['Authorization'];
    }
  }, [token]);

  return { user, token, isAdmin, authReady, login, logout };
}
