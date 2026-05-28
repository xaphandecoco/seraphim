import { useEffect } from 'react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';
import type { User } from '@/types';

const STORAGE_KEY = 'seraphim_auth';

interface StoredAuth {
  user: User;
  token: string;
}

export function useAuth() {
  const { user, token, isAdmin, login, logout } = useAuthStore();

  // Hydrate from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      try {
        const parsed: StoredAuth = JSON.parse(stored);
        if (parsed.token && parsed.user) {
          login(parsed.user, parsed.token);
        }
      } catch {
        localStorage.removeItem(STORAGE_KEY);
      }
    }
  }, [login]);

  // Persist to localStorage on change
  useEffect(() => {
    if (user && token) {
      const data: StoredAuth = { user, token };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, [user, token]);

  // Update API auth header when token changes
  useEffect(() => {
    if (token) {
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    } else {
      delete api.defaults.headers.common['Authorization'];
    }
  }, [token]);

  return { user, token, isAdmin, login, logout };
}
