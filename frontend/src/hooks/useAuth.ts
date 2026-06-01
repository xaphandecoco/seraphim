import { useEffect, useRef } from 'react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import type { User } from '@/types';

export function useAuth() {
  const { user, token, isAdmin, login, logout } = useAuthStore();
  const initialized = useRef(false);

  // On app load: try to get a new access token from the HttpOnly refresh cookie.
  // This replaces the localStorage persistence pattern — token stays in memory only.
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    if (token) return; // already hydrated (e.g. just logged in)

    api.post<{ access_token: string }>('/auth/refresh')
      .then(async (res) => {
        const accessToken = res.data.access_token;
        const meRes = await api.get<User>('/auth/me', {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        login(meRes.data, accessToken);
      })
      .catch(() => {
        // No valid refresh cookie — user must log in
      });
  }, [token, login]);

  // Sync the Authorization header whenever the in-memory token changes
  useEffect(() => {
    if (token) {
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    } else {
      delete api.defaults.headers.common['Authorization'];
    }
  }, [token]);

  return { user, token, isAdmin, login, logout };
}
