import { create } from 'zustand';
import type { User } from '@/types';

interface AuthState {
  user: User | null;
  token: string | null;
  isAdmin: boolean;
  isAuthenticated: boolean;
  /** false until the initial /auth/refresh attempt completes; route guards wait for this */
  authReady: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  setUser: (user: User | null) => void;
  setToken: (token: string) => void;
  setAuthReady: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  isAdmin: false,
  isAuthenticated: false,
  authReady: false,
  login: (user, token) => {
    set({ user, token, isAdmin: user.role === 'admin', isAuthenticated: true, authReady: true });
  },
  logout: () => {
    set({ user: null, token: null, isAdmin: false, isAuthenticated: false, authReady: true });
  },
  setUser: (user) => {
    set({ user, isAdmin: user?.role === 'admin', isAuthenticated: !!user });
  },
  setToken: (token) => {
    set({ token, isAuthenticated: true });
  },
  setAuthReady: () => {
    set({ authReady: true });
  },
}));
